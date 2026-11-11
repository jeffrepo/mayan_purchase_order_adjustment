import math

from markupsafe import Markup

from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    def _mayan_compare_qty(self, quantity, other):
        self.ensure_one()
        return float_compare(quantity, other, precision_rounding=self.product_uom.rounding)

    def _mayan_pending_moves(self):
        return self.move_ids.filtered(lambda move: move.state not in ("done", "cancel"))

    def _mayan_validate_adjustment(self, delete=False):
        self.ensure_one()
        self.check_access("write")
        if self.order_id.state not in ("purchase", "done") or not self.order_id.mayan_has_receipts:
            raise UserError(_("Solo se pueden ajustar órdenes confirmadas con recepciones realizadas."))
        if self.display_type or self.qty_received_method != "stock_moves":
            raise UserError(_("El ajuste está disponible únicamente para productos con movimientos de inventario."))
        if self._mayan_compare_qty(self.product_qty, self.qty_received) <= 0:
            raise UserError(_("El producto %s no tiene cantidad pendiente.", self.product_id.display_name))
        if self._mayan_compare_qty(self.qty_received, 0) < 0:
            raise UserError(_("Revise las devoluciones antes de ajustar esta línea."))
        if delete:
            if self.move_ids.filtered(lambda move: move.state == "done") or self.qty_received:
                raise UserError(_("No se puede eliminar una línea con movimientos realizados, aunque se haya devuelto el producto."))
            if self.invoice_lines:
                raise UserError(_("No se puede eliminar una línea vinculada a facturas. Revise primero la facturación."))
        elif self._mayan_compare_qty(self.qty_invoiced, self.qty_received) > 0:
            raise UserError(_("La cantidad facturada supera la recibida. Corrija la facturación antes de cerrar el pendiente."))

        pending = self._mayan_pending_moves()
        if any(
            move.product_id != self.product_id
            or move.location_id.usage != "supplier"
            or move.location_dest_id.usage not in ("internal", "transit")
            or move.origin_returned_move_id
            or move.move_orig_ids
            or move.move_dest_ids
            for move in pending
        ) or self.move_dest_ids:
            raise UserError(_(
                "Este ajuste requiere una recepción directa del proveedor al inventario. "
                "Revise por separado las devoluciones, kits, entregas directas y rutas encadenadas."
            ))
        if pending.filtered("picked"):
            raise UserError(_("Hay operaciones pendientes marcadas como recogidas. Revise la recepción antes de ajustar la compra."))
        return pending

    def _mayan_audit_body(self, delete, moves):
        self.ensure_one()
        return Markup(
            "<p><strong>%s</strong></p><ul>"
            "<li>%s</li><li>%s</li><li>%s</li><li>%s</li><li>%s</li></ul>"
        ) % (
            _("Ajuste de pendientes de compra"),
            _("Producto: %s", self.product_id.display_name),
            _("Acción: %s", _("Eliminar línea sin recepción") if delete else _("Cerrar cantidad pendiente")),
            _("Cantidad solicitada: %(old)s → %(new)s %(unit)s. Recibida: %(received)s.",
              old=self.product_qty, new=0 if delete else self.qty_received,
              unit=self.product_uom.display_name, received=self.qty_received),
            _("Recepciones pendientes canceladas: %s", ", ".join(moves.picking_id.mapped("name")) or _("Ninguna")),
            _("Motivo: %s", self.env.context.get("mayan_adjustment_reason") or _("Edición directa de la orden")),
        )

    @api.model_create_multi
    def create(self, vals_list):
        orders = self.env["purchase.order"].browse({
            vals.get("order_id") or self.env.context.get("default_order_id")
            for vals in vals_list
            if vals.get("order_id") or self.env.context.get("default_order_id")
        })
        orders.check_access("write")
        # Also check after defaults have been applied; imports must obey this rule.
        for vals in vals_list:
            order = orders.filtered(lambda record: record.id == (
                vals.get("order_id") or self.env.context.get("default_order_id")
            ))
            if order.mayan_has_receipts and not vals.get(
                "display_type", self.env.context.get("default_display_type")
            ):
                raise UserError(_("No se pueden agregar productos a una orden que ya tiene recepciones."))
        lines = super().create(vals_list)
        if any(not line.display_type and line.order_id.mayan_has_receipts for line in lines):
            raise UserError(_("No se pueden agregar productos a una orden que ya tiene recepciones."))
        return lines

    def write(self, vals):
        protected = self.filtered(lambda line: not line.display_type and line.order_id.mayan_has_receipts)
        for line in self:
            target_order = self.env["purchase.order"].browse(vals.get("order_id"))
            if "order_id" in vals and vals["order_id"] != line.order_id.id and (
                line.order_id.mayan_has_receipts or target_order.mayan_has_receipts
            ):
                raise UserError(_("No se pueden trasladar líneas desde o hacia una orden con recepciones."))
        for line in protected:
            if any(field in vals and vals[field] != line[field].id for field in ("product_id", "product_uom")):
                raise UserError(_("No se puede cambiar el producto ni su unidad de medida después de una recepción."))
            if "product_packaging_qty" in vals and vals["product_packaging_qty"] != line.product_packaging_qty:
                raise UserError(_("Utilice Ajustar pendientes para modificar la cantidad después de una recepción."))
        if "product_qty" not in vals:
            return super().write(vals)

        adjustments = protected.filtered(lambda line: vals["product_qty"] != line.product_qty)
        if not adjustments:
            return super().write(vals)
        adjustments.order_id._mayan_lock_for_adjustment()
        audits = []
        for line in adjustments:
            if not math.isfinite(vals["product_qty"]) or line._mayan_compare_qty(vals["product_qty"], line.qty_received) != 0:
                raise UserError(_(
                    "Para cerrar el pendiente, la cantidad solicitada debe ser igual a la recibida (%s). "
                    "Utilice Ajustar pendientes.", line.qty_received
                ))
            moves = line._mayan_validate_adjustment()
            audits.append((line, moves, line._mayan_audit_body(False, moves)))

        # Validate the complete recordset before cancelling a single movement.
        for line, moves, body in audits:
            moves._action_cancel()
            values = dict(vals, product_qty=line.qty_received)
            # Quantity-dependent vendor prices must not reprice received goods.
            for field in ("price_unit", "discount", "date_planned", "name"):
                values.setdefault(field, line[field])
            super(PurchaseOrderLine, line).write(values)
            line.order_id.message_post(body=body, subtype_xmlid="mail.mt_note")
        if self - adjustments:
            super(PurchaseOrderLine, self - adjustments).write(vals)
        return True

    @api.ondelete(at_uninstall=False)
    def _unlink_except_purchase_or_done(self):
        permitted = self.filtered(
            lambda line: not line.display_type
            and line.order_id.state in ("purchase", "done")
            and line.order_id.mayan_has_receipts
        )
        for line in permitted:
            line._mayan_validate_adjustment(delete=True)
        return super(PurchaseOrderLine, self - permitted)._unlink_except_purchase_or_done()

    def unlink(self):
        products = self.filtered(lambda line: not line.display_type)
        products.check_access("unlink")
        if any(line.move_ids.filtered(lambda move: move.state == "done") or line.qty_received for line in products):
            raise UserError(_("No se puede eliminar una línea con recepciones realizadas. Ajuste su cantidad pendiente."))
        protected = products.filtered(lambda line: line.order_id.mayan_has_receipts)
        protected.order_id._mayan_lock_for_adjustment()
        audits = []
        for line in protected:
            moves = line._mayan_validate_adjustment(delete=True)
            audits.append((line.order_id, line._mayan_audit_body(True, moves)))
        # purchase_stock cancels the validated movements before removing the line.
        result = super().unlink()
        for order, body in audits:
            order.message_post(body=body, subtype_xmlid="mail.mt_note")
        return result
