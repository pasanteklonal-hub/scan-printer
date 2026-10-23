Modo de uso:

- Ejecutar scanTK.py

Se abrira una interfaz grafica. Donde se pueden agregar o modificar ip de impresoras SAMSUNG.

La pestaña "Agregar comando" permite registrar marca, modelo y un script/comando. Indicá una IP
accesible para la prueba e incluí `{ip}` en el comando donde se debe insertar esa dirección.
Antes de guardar, la aplicación ejecuta el comando y comprueba que imprima un objeto JSON con
`toner` y `paginas`, ambos objetos. Por ejemplo:
`{"toner":{"Black":80},"paginas":{"impresiones_totales":12345}}`. Solo se habilita el guardado
cuando la prueba es correcta y el formulario no cambió desde la prueba. La aplicación completa
los campos comunes (`ip`, `marca`, `modelo`, `online`, `estado`, `mac`, `numero_serie` y `error`)
para integrarlo con los resultados de los demás escáneres. Los comandos se guardan en
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
