from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    mayan_has_receipts = fields.Boolean(
        string="Tiene movimientos recibidos",
        compute="_compute_mayan_has_receipts",
    )

    @api.depends("order_line.move_ids.state", "order_line.qty_received")
    def _compute_mayan_has_receipts(self):
        for order in self:
            # Keep the protection after a full return: net received can be zero.
            order.mayan_has_receipts = any(
                move.state == "done" for move in order.order_line.move_ids
            ) or any(line.qty_received for line in order.order_line)

    def _mayan_lock_for_adjustment(self):
        """Serialize edits and receipt validation without elevating permissions."""
        self.check_access("write")
        if not self:
            return
        self.env["purchase.order.line"].flush_model()
        self.env["stock.move"].flush_model()
        self.flush_recordset()
        self.env.cr.execute(
            "SELECT id FROM purchase_order WHERE id IN %s ORDER BY id FOR UPDATE",
            [tuple(self.ids)],
        )
        self.env.cr.execute(
            "SELECT id FROM purchase_order_line WHERE order_id IN %s "
            "ORDER BY id FOR UPDATE",
            [tuple(self.ids)],
        )
        self.env.cr.execute(
            "SELECT m.id FROM stock_move m JOIN purchase_order_line l "
            "ON l.id = m.purchase_line_id WHERE l.order_id IN %s "
            "ORDER BY m.id FOR UPDATE OF m",
            [tuple(self.ids)],
        )
        self.invalidate_recordset(["order_line", "state", "mayan_has_receipts"])
        self.order_line.invalidate_recordset()
        self.order_line.move_ids.invalidate_recordset()

    def _mayan_adjustable_lines(self):
        self.ensure_one()
        return self.order_line.filtered(
            lambda line: not line.display_type
            and line.qty_received_method == "stock_moves"
            and line._mayan_compare_qty(line.product_qty, line.qty_received) > 0
        )

    def action_mayan_adjust_pending(self):
        self.ensure_one()
        self.check_access("write")
        if self.state not in ("purchase", "done") or not self.mayan_has_receipts:
            raise UserError(_("La orden debe estar confirmada y tener una recepción realizada."))
        if not self._mayan_adjustable_lines():
            raise UserError(_("No hay cantidades pendientes para ajustar en esta orden."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Ajustar pendientes de compra"),
            "res_model": "mayan.purchase.order.adjustment",
            "view_mode": "form",
            "views": [(
                self.env.ref(
                    "mayan_purchase_order_adjustment.purchase_order_adjustment_form"
                ).id,
                "form",
            )],
            "target": "new",
            "context": {"default_order_id": self.id},
        }
