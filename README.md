# Mayan - Ajustes de órdenes de compra

Módulo independiente para **Odoo 18**. Permite cerrar las cantidades de una orden
de compra que el proveedor ya no entregará después de una recepción parcial.
La raíz del repositorio es directamente la raíz del addon
`mayan_purchase_order_adjustment`.

## Alcance del desarrollo No. 5

| Caso | Resultado |
| --- | --- |
| A: línea sin ninguna recepción | Elimina la línea y cancela sus movimientos pendientes. |
| B: 100 solicitados, 50 recibidos, 50 pendientes | Conserva la línea con 50 solicitados y cancela el saldo. |
| B.1: línea con recepción parcial | Impide eliminarla y conserva sus movimientos realizados. |
| B.2: historial | Nota interna con producto, cantidades anterior/nueva/recibida, recepciones afectadas y motivo; Odoo registra autor y fecha. |
| B.3: backorder | Queda sin cantidad activa pendiente para las líneas ajustadas. |
| B.4: productos nuevos | Bloquea nuevas líneas de producto y sustituciones después de una recepción, incluso por importación/API. |

El cierre es total para cada línea seleccionada: la cantidad solicitada queda
igual a la recibida. No permite aumentarla ni reducirla a una cantidad intermedia
que mantenga un saldo pendiente. Las líneas que no se seleccionen siguen abiertas.

## Instalación

1. Clonar el repositorio como `<ruta_addons>/mayan_purchase_order_adjustment`.
2. Incluir `<ruta_addons>` en `addons_path` y reiniciar Odoo.
3. Actualizar la lista de aplicaciones e instalar **Mayan - Ajustes de órdenes de compra**.

Dependencia: `purchase_stock` (Compras e Inventario). No requiere módulos privados
de Mayan. Se aplica a las compañías donde esté instalado; respeta sus reglas de
acceso y no usa `sudo()` para modificar órdenes o movimientos.

Actualización:

```bash
odoo-bin -d NOMBRE_BD -u mayan_purchase_order_adjustment --stop-after-init
```

## Uso

1. Abrir una orden confirmada con al menos una recepción realizada.
2. Pulsar **Ajustar pendientes**.
3. Indicar el motivo y marcar los productos que ya no entregará el proveedor.
4. Revisar las cantidades y el resultado previsto; pulsar **Aplicar ajuste**.
5. Consultar el historial de la orden y las recepciones pendientes.

El botón también funciona en órdenes bloqueadas, sin desbloquearlas. Requiere
permisos de Compras y de operación sobre el inventario afectado (usuario de
Compras y usuario de Inventario). Las ediciones directas de cantidad y la
eliminación directa de líneas usan las mismas validaciones y registran historial.

## Reglas de integridad

- Una recepción realizada nunca se cancela ni se modifica por este módulo.
- Una línea con movimientos realizados no se elimina, incluso si una devolución
  deja su cantidad neta recibida en cero.
- No se puede cerrar por debajo de la cantidad facturada neta. Se debe corregir
  primero la factura/nota de crédito. Tampoco se elimina una línea vinculada a
  facturas, aunque estén canceladas, para conservar su relación documental.
- No se permiten cambios de producto, unidad de medida ni traslado de líneas
  entre órdenes con recepciones. Las notas y secciones siguen disponibles.
- Se conservan precio, descuento, descripción y fecha previstos al cerrar una
  cantidad; el cambio no vuelve a aplicar la tarifa del proveedor.
- Se valida todo lo seleccionado antes de modificarlo. Si cambian las cantidades
  mientras el asistente está abierto, se exige abrirlo nuevamente.
- El ajuste bloquea las filas de órdenes, líneas y movimientos durante la
  transacción para coordinarse con otras ediciones y validaciones de recepción.
- No se cambian las reglas estándar de órdenes que aún no tienen recepciones.

Las transferencias canceladas conservan su demanda original como historial de
Odoo. **Pendiente cero** significa que no quedan movimientos activos por recibir
para la línea ajustada; no se borran las transferencias ni se reescribe su historia.
Si un backorder contiene otras líneas pendientes, la transferencia permanece
abierta para esas líneas.

## Límites de esta versión

El cierre automático admite recepciones directas del proveedor a inventario
(ubicación interna o de tránsito). Bloquea ajustes con movimientos pendientes de
devolución, dropshipping, componentes de kits o rutas encadenadas/MTO, para evitar
cancelar abastecimientos relacionados. También bloquea movimientos pendientes ya
marcados como recogidos. Estos casos requieren revisar el flujo de inventario.

No incluye ajustes de servicios, modificaciones automáticas de facturas ni un
flujo adicional de aprobación. Las devoluciones ya realizadas conservan su
historial y se utiliza la cantidad neta recibida calculada por Odoo.

## Pruebas

La integración de GitHub Actions instala el addon en Odoo 18 con PostgreSQL 16 y
ejecuta las pruebas de `tests/test_purchase_order_adjustment.py`.

Ejecución equivalente en una **base de pruebas**:

```bash
odoo-bin -d mayan_purchase_test -i mayan_purchase_order_adjustment \
  --without-demo=all --test-enable \
  --test-tags=/mayan_purchase_order_adjustment --stop-after-init
```

La suite comprueba recepciones reales y backorders, borrado de líneas sin ingreso,
historial, facturación, devoluciones, permisos, conversión de unidades, precios,
asistentes desactualizados y conservación de movimientos y valoración realizados.

Antes de instalar en producción, validar en una copia de la base de Mayan con
sus módulos adicionales y los permisos de los operadores habituales.
