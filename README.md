Modo de uso:

- Ejecutar scanTK.py

Se abrira una interfaz grafica. Donde se pueden agregar o modificar ip de impresoras SAMSUNG.

La pestaña "Agregar comando" permite registrar una marca específica. El comando debe usar `{ip}`
como marcador de la dirección consultada e imprimir un objeto JSON por salida estándar. En
"Salida esperada" se declaran los campos como `campo:tipo`, separados por coma y en el orden
esperado. Se admiten `string`, `integer`, `number`, `boolean`, `object`, `array` y `null`.
Por ejemplo: `modelo:string,numero_serie:string,toner:object`. La aplicación valida que cada
campo exista y tenga el tipo declarado, y agrega `ip`, `marca` y los campos comunes al JSON final.
Los comandos se guardan en
`estado-impresoras/comandos_impresoras.json`.

Una vez que se ejecuto el json del escaneo, se guardara por fecha.


Para tener un archivo en hoja de calculos
- Utilizar el script actualizar_listado.py

Este va a guardar en una hoja de calculo segun el ultimo json guardado por fecha mas actual. Siempre que se ejecute este archivo, se va a actualizar dicho archivo "impresoras_al_dia.xlsx"

Info adicional:
"impresoras.xlsx" se usa como archivo base para chequear las mac. No eliminar.


Conclusion:

1- Usar scanTK.py para relevar las impresoras alquiladas.
2- Usar actualizar_listado.py para pasar a hoja de calculo.

Para empaquetar la aplicación como ejecutable, consultar [EMPAQUETADO.md](EMPAQUETADO.md).
