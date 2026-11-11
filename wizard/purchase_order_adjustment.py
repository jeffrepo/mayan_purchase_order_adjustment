from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError


class PurchaseOrderAdjustment(models.TransientModel):
    _name = "mayan.purchase.order.adjustment"
    _description = "Ajuste de pendientes de compra Mayan"

    order_id = fields.Many2one("purchase.order", string="Orden de compra", required=True, readonly=True)
    reason = fields.Text(string="Motivo", required=True)
    line_ids = fields.One2many("mayan.purchase.order.adjustment.line", "wizard_id", string="Pendientes")

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        order = self.env["purchase.order"].browse(values.get("order_id"))
        if order and "line_ids" in field_names:
            order.check_access("write")
            values["line_ids"] = [Command.create({
                "purchase_line_id": line.id,
                "ordered_qty": line.product_qty,
                "received_qty": line.qty_received,
            }) for line in order._mayan_adjustable_lines()]
        return values

    def action_apply(self):
        self.ensure_one()
        self.check_access("write")
        if not (self.reason or "").strip():
            raise UserError(_("Indique el motivo del ajuste."))
        selected = self.line_ids.filtered("selected")
        if not selected:
            raise UserError(_("Seleccione al menos una línea para ajustar."))
        order = self.order_id
        order._mayan_lock_for_adjustment()
        lines = selected.purchase_line_id.exists()
        if len(lines) != len(selected) or any(line.order_id != order for line in lines):
            raise UserError(_("Las líneas deben existir, pertenecer a esta orden y no repetirse."))
        for item in selected:
            line = item.purchase_line_id
            if line._mayan_compare_qty(line.product_qty, item.ordered_qty) or line._mayan_compare_qty(line.qty_received, item.received_qty):
                raise UserError(_("La orden o sus recepciones cambiaron. Cierre y abra nuevamente el asistente."))
            delete = not line.move_ids.filtered(lambda move: move.state == "done") and not line.qty_received
            line._mayan_validate_adjustment(delete=delete)

        # All selected lines are checked before applying any change.
        for line in lines.with_context(mayan_adjustment_reason=self.reason.strip()):
            if not line.move_ids.filtered(lambda move: move.state == "done") and not line.qty_received:
                line.unlink()
            else:
                line.write({"product_qty": line.qty_received})
        return {"type": "ir.actions.act_window_close"}


class PurchaseOrderAdjustmentLine(models.TransientModel):
    _name = "mayan.purchase.order.adjustment.line"
    _description = "Línea de ajuste de pendientes de compra Mayan"

    wizard_id = fields.Many2one("mayan.purchase.order.adjustment", required=True, ondelete="cascade")
    selected = fields.Boolean(string="Ajustar")
    purchase_line_id = fields.Many2one("purchase.order.line", string="Línea de compra", required=True, readonly=True, ondelete="cascade")
    product_id = fields.Many2one(related="purchase_line_id.product_id", string="Producto")
    product_uom_id = fields.Many2one(related="purchase_line_id.product_uom", string="Unidad")
    ordered_qty = fields.Float(string="Solicitada", digits="Product Unit of Measure", readonly=True)
    received_qty = fields.Float(string="Recibida", digits="Product Unit of Measure", readonly=True)
    pending_qty = fields.Float(string="Pendiente a cancelar", compute="_compute_preview", digits="Product Unit of Measure")
    operation = fields.Char(string="Resultado", compute="_compute_preview")

    @api.depends("ordered_qty", "received_qty", "purchase_line_id.move_ids.state")
    def _compute_preview(self):
        for item in self:
            item.pending_qty = item.ordered_qty - item.received_qty
            has_history = item.purchase_line_id.move_ids.filtered(lambda move: move.state == "done")
            item.operation = _("Conservar línea y ajustar a lo recibido") if has_history or item.received_qty else _("Eliminar línea sin recepción")
