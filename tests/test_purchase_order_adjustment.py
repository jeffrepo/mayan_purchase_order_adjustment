from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestPurchaseOrderAdjustment(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.po_lock = "edit"
        cls.vendor = cls.env["res.partner"].create({"name": "Proveedor Mayan QA"})
        cls.products = cls.env["product.product"].create([
            {"name": "Producto parcial", "type": "consu", "is_storable": True, "purchase_method": "receive"},
            {"name": "Producto sin recepción", "type": "consu", "is_storable": True, "purchase_method": "receive"},
        ])
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.env.company.id)], limit=1)
        cls.warehouse.in_type_id.create_backorder = "always"

    def _line_vals(self, product, quantity):
        return {
            "product_id": product.id,
            "name": product.display_name,
            "product_qty": quantity,
            "product_uom": product.uom_po_id.id,
            "price_unit": 10,
            "date_planned": fields.Datetime.now(),
            "taxes_id": [Command.clear()],
        }

    def _order(self, receive=True, quantities=(100, 20)):
        order = self.env["purchase.order"].create({
            "partner_id": self.vendor.id,
            "picking_type_id": self.warehouse.in_type_id.id,
            "order_line": [Command.create(self._line_vals(product, quantity))
                           for product, quantity in zip(self.products, quantities)],
        })
        order.button_confirm()
        if receive:
            self._receive(order.picking_ids, {self.products[0].id: 50})
        return order

    def _receive(self, picking, quantities):
        for move in picking.move_ids.filtered(lambda record: record.state not in ("done", "cancel")):
            quantity = quantities.get(move.product_id.id, 0)
            move.quantity = quantity
            move.picked = bool(quantity)
        picking.button_validate()
        self.assertEqual(picking.state, "done")

    def _wizard(self, order, selected=None):
        wizard = Form(order.env["mayan.purchase.order.adjustment"].with_context(default_order_id=order.id))
        wizard.reason = "El proveedor no surtirá el saldo."
        wizard = wizard.save()
        selected = order.order_line if selected is None else selected
        wizard.line_ids.filtered(lambda item: item.purchase_line_id in selected).selected = True
        return wizard

    def _active(self, lines):
        return lines.move_ids.filtered(lambda move: move.state not in ("done", "cancel"))

    def _bill(self, line, quantity):
        return self.env["account.move"].create({
            "move_type": "in_invoice",
            "partner_id": self.vendor.id,
            "journal_id": self.company_data["default_journal_purchase"].id,
            "invoice_line_ids": [Command.create({
                "product_id": line.product_id.id,
                "purchase_line_id": line.id,
                "product_uom_id": line.product_uom.id,
                "quantity": quantity,
                "price_unit": line.price_unit,
                "account_id": self.company_data["default_account_expense"].id,
            })],
        })

    def test_close_partial_and_delete_unreceived(self):
        order = self._order()
        partial, unreceived = order.order_line
        received_move = partial.move_ids.filtered(lambda move: move.state == "done")
        received_snapshot = received_move.read(["quantity", "product_uom_qty", "state", "date"])
        valuation = received_move.stock_valuation_layer_ids.read(["quantity", "value"])
        pending = self._active(order.order_line)
        self._wizard(order).action_apply()
        self.assertEqual(partial.product_qty, 50)
        self.assertEqual(partial.qty_received, 50)
        self.assertFalse(unreceived.exists())
        self.assertFalse(self._active(partial))
        self.assertEqual(set(pending.mapped("state")), {"cancel"})
        self.assertEqual(received_snapshot, received_move.read(["quantity", "product_uom_qty", "state", "date"]))
        self.assertEqual(valuation, received_move.stock_valuation_layer_ids.read(["quantity", "value"]))
        self.assertEqual(order.amount_untaxed, 500)

    def test_direct_quantity_edit_cancels_only_selected_line(self):
        order = self._order()
        partial, other = order.order_line
        other_moves = self._active(other)
        partial.write({"product_qty": 50})
        self.assertFalse(self._active(partial))
        self.assertEqual(self._active(other), other_moves)
        self.assertEqual(other_moves.product_uom_qty, 20)

    def test_delete_unreceived_line_directly(self):
        order = self._order()
        line = order.order_line[1]
        moves = line.move_ids
        line.unlink()
        self.assertFalse(line.exists())
        self.assertEqual(set(moves.mapped("state")), {"cancel"})

    def test_partial_line_cannot_be_deleted(self):
        line = self._order().order_line[0]
        with self.assertRaises(UserError):
            line.unlink()
        self.assertTrue(line.exists())
        self.assertTrue(self._active(line))

    def test_invalid_quantities_leave_backorder_intact(self):
        line = self._order().order_line[0]
        for quantity in (49, -1, 0, 75, 101, float("inf"), float("nan")):
            with self.subTest(quantity=quantity), self.assertRaises(UserError):
                line.write({"product_qty": quantity})
        self.assertEqual(line.product_qty, 100)
        self.assertEqual(self._active(line).product_uom_qty, 50)

    def test_new_product_blocked_by_create_and_order_commands(self):
        order = self._order()
        values = self._line_vals(self.products[0], 3)
        with self.assertRaises(UserError):
            self.env["purchase.order.line"].create(dict(values, order_id=order.id))
        with self.assertRaises(UserError):
            order.write({"order_line": [Command.create(values)]})
        with self.assertRaises(UserError):
            self.env["purchase.order.line"].with_context(default_order_id=order.id).create(values)

    def test_product_uom_and_order_replacement_blocked(self):
        order = self._order()
        line = order.order_line[1]
        another = self._order(receive=False)
        for values in ({"product_id": self.products[0].id},
                       {"product_uom": self.env.ref("uom.product_uom_dozen").id},
                       {"order_id": another.id}, {"product_packaging_qty": 2}):
            with self.subTest(values=values), self.assertRaises(UserError):
                line.write(values)
        with self.assertRaises(UserError):
            another.order_line[0].write({"order_id": order.id})

    def test_notes_and_draft_orders_still_work(self):
        order = self._order()
        note = self.env["purchase.order.line"].create({
            "order_id": order.id, "display_type": "line_note", "name": "Nota de compra",
        })
        note.unlink()
        draft = order.copy()
        self.assertEqual(draft.state, "draft")
        self.assertFalse(draft.mayan_has_receipts)
        draft.order_line[0].write({"product_qty": 123})
        draft.order_line[1].unlink()
        draft.write({"order_line": [Command.create(self._line_vals(self.products[1], 7))]})
        self.assertEqual(len(draft.order_line), 2)

    def test_confirmed_order_without_receipt_uses_standard_rules(self):
        order = self._order(receive=False)
        order.order_line[0].write({"product_qty": 110})
        order.write({"order_line": [Command.create(self._line_vals(self.products[1], 3))]})
        with self.assertRaises(UserError):
            order.action_mayan_adjust_pending()
        with self.assertRaises(UserError):
            order.order_line[0].unlink()

    def test_locked_order_can_be_adjusted_without_unlocking(self):
        order = self._order()
        order.button_done()
        self._wizard(order).action_apply()
        self.assertEqual(order.state, "done")
        self.assertEqual(order.order_line.product_qty, 50)
        self.assertFalse(self._active(order.order_line))

    def test_invoiced_quantity_prevents_reduction(self):
        order = self._order()
        line = order.order_line[0]
        self._bill(line, 75)
        with self.assertRaises(UserError):
            self._wizard(order).action_apply()
        self.assertEqual(line.product_qty, 100)
        self.assertEqual(len(order.order_line), 2)
        self.assertEqual(self._active(line).product_uom_qty, 50)

    def test_invoiced_unreceived_line_cannot_be_deleted(self):
        order = self._order()
        self._bill(order.order_line[1], 20)
        with self.assertRaises(UserError):
            self._wizard(order).action_apply()
        self.assertEqual(order.order_line[0].product_qty, 100)
        self.assertEqual(len(order.order_line), 2)

    def test_received_and_invoiced_quantity_can_close(self):
        order = self._order()
        line = order.order_line[0]
        bill = self._bill(line, 50)
        self._wizard(order, line).action_apply()
        self.assertEqual(line.product_qty, 50)
        self.assertEqual(bill.invoice_line_ids.quantity, 50)

    def test_stale_wizard_rejected_after_second_receipt(self):
        order = self._order()
        wizard = self._wizard(order)
        backorder = order.picking_ids.filtered(lambda picking: picking.state not in ("done", "cancel"))
        self._receive(backorder, {self.products[0].id: 10})
        with self.assertRaises(UserError):
            wizard.action_apply()
        self.assertEqual(order.order_line[0].product_qty, 100)
        self.assertEqual(order.order_line[0].qty_received, 60)

    def test_foreign_line_rejected(self):
        order = self._order()
        another = self._order()
        wizard = self._wizard(order, order.order_line[:0])
        wizard.line_ids[0].write({"selected": True, "purchase_line_id": another.order_line[0].id})
        with self.assertRaises(UserError):
            wizard.action_apply()
        self.assertEqual(another.order_line[0].product_qty, 100)

    def test_reason_and_selection_required(self):
        order = self._order()
        wizard = self._wizard(order, order.order_line[:0])
        with self.assertRaises(UserError):
            wizard.action_apply()
        wizard.line_ids.selected = True
        wizard.reason = "  "
        with self.assertRaises(UserError):
            wizard.action_apply()

    def test_audit_contains_author_date_quantities_and_escaped_reason(self):
        order = self._order()
        wizard = self._wizard(order, order.order_line[0])
        wizard.reason = "<script>alert(1)</script> proveedor sin stock"
        wizard.action_apply()
        message = order.message_ids.filtered(lambda msg: "Ajuste de pendientes de compra" in str(msg.body))[0]
        self.assertEqual(message.author_id, self.env.user.partner_id)
        self.assertTrue(message.date)
        self.assertIn("100", message.body)
        self.assertIn("50", message.body)
        self.assertIn("proveedor sin stock", message.body)
        self.assertNotIn("<script>", message.body)

    def test_purchase_and_inventory_user_can_adjust(self):
        user = new_test_user(self.env, login="mayan_purchase_operator", groups="purchase.group_purchase_user,stock.group_stock_user")
        order = self._order()
        wizard = self._wizard(order.with_user(user))
        wizard.with_user(user).action_apply()
        self.assertEqual(order.order_line.product_qty, 50)
        message = order.message_ids.filtered(lambda msg: "Ajuste de pendientes de compra" in str(msg.body))[0]
        self.assertEqual(message.author_id, user.partner_id)

    def test_inventory_only_user_cannot_adjust(self):
        user = new_test_user(self.env, login="mayan_inventory_operator", groups="stock.group_stock_user")
        order = self._order()
        with self.assertRaises(AccessError):
            order.with_user(user).action_mayan_adjust_pending()

    def test_quantity_unit_conversion(self):
        order = self._order(receive=False, quantities=(10, 20))
        line = order.order_line[0]
        # Build a fresh order in dozens before confirmation, so stock uses units.
        order.button_cancel()
        order.button_draft()
        line.write({"product_uom": self.env.ref("uom.product_uom_dozen").id})
        order.button_confirm()
        picking = order.picking_ids.filtered(lambda record: record.state not in ("done", "cancel"))
        move = picking.move_ids.filtered(lambda record: record.purchase_line_id == line)
        quantity = line.product_uom._compute_quantity(4, move.product_uom)
        self._receive(picking, {line.product_id.id: quantity})
        self.assertEqual(line.qty_received, 4)
        self._wizard(order, line).action_apply()
        self.assertEqual(line.product_qty, 4)
        self.assertFalse(self._active(line))

    def test_vendor_price_and_description_preserved(self):
        order = self._order()
        line = order.order_line[0]
        line.write({"price_unit": 9.25, "discount": 5, "name": "Descripción negociada"})
        self.env["product.supplierinfo"].create({
            "partner_id": self.vendor.id, "product_tmpl_id": line.product_id.product_tmpl_id.id,
            "min_qty": 1, "price": 25,
        })
        expected = (line.price_unit, line.discount, line.name, line.date_planned)
        self._wizard(order, line).action_apply()
        self.assertEqual(expected, (line.price_unit, line.discount, line.name, line.date_planned))

    def test_picked_pending_move_rejected(self):
        order = self._order()
        self._active(order.order_line[0]).picked = True
        with self.assertRaises(UserError):
            self._wizard(order).action_apply()
        self.assertEqual(order.order_line[0].product_qty, 100)

    def test_returned_line_retains_history_and_blocks_new_products(self):
        order = self._order()
        receipt = order.picking_ids.filtered(lambda picking: picking.state == "done")
        returns = Form(self.env["stock.return.picking"].with_context(
            active_model="stock.picking", active_id=receipt.id, active_ids=receipt.ids,
        )).save()
        returns.product_return_moves.write({"quantity": 50, "to_refund": True})
        action = returns.action_create_returns()
        picking = self.env["stock.picking"].browse(action["res_id"])
        self._receive(picking, {self.products[0].id: 50})
        line = order.order_line[0]
        self.assertEqual(line.qty_received, 0)
        self.assertTrue(order.mayan_has_receipts)
        with self.assertRaises(UserError):
            line.unlink()
        with self.assertRaises(UserError):
            order.write({"order_line": [Command.create(self._line_vals(self.products[0], 2))]})
        self._wizard(order, line).action_apply()
        self.assertTrue(line.exists())
        self.assertEqual(line.product_qty, 0)
        self.assertEqual(len(line.move_ids.filtered(lambda move: move.state == "done")), 2)

    def test_routed_receipt_rejected_without_mutation(self):
        order = self._order()
        pending = self._active(order.order_line[0])
        downstream = self.env["stock.move"].create({
            "name": "Traslado encadenado", "product_id": pending.product_id.id,
            "product_uom": pending.product_uom.id, "product_uom_qty": 50,
            "location_id": pending.location_dest_id.id,
            "location_dest_id": self.env.ref("stock.stock_location_customers").id,
            "move_orig_ids": [Command.link(pending.id)],
        })
        with self.assertRaises(UserError):
            self._wizard(order).action_apply()
        self.assertEqual(downstream.state, "draft")
        self.assertEqual(order.order_line[0].product_qty, 100)

    def test_cancelled_backorder_not_recreated(self):
        order = self._order()
        line = order.order_line[0]
        self._active(line)._action_cancel()
        self._wizard(order, line).action_apply()
        line.write({"price_unit": 12, "name": "Actualización posterior"})
        line._create_or_update_picking()
        self.assertFalse(self._active(line))
        self.assertEqual(line.product_qty, 50)
