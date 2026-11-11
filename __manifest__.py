{
    "name": "Mayan - Ajustes de órdenes de compra",
    "summary": "Cierre de pendientes de compra después de una recepción parcial",
    "version": "18.0.1.0.0",
    "category": "Inventory/Purchase",
    "author": "Mayan Golf Club",
    "website": "https://github.com/jeffrepo/mayan_purchase_order_adjustment",
    "license": "LGPL-3",
    "depends": ["purchase_stock"],
    "data": [
        "security/ir.model.access.csv",
        "wizard/purchase_order_adjustment_views.xml",
        "views/purchase_order_views.xml",
    ],
    "application": True,
    "installable": True,
}
