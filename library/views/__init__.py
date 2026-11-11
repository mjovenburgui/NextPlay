"""Vistas de la biblioteca, un archivo por pantalla:

- ficha_del_juego.py: «Tu biblioteca» en la Ficha del juego (estado, nota y quitar) y su sección de reseñas.
- escribir_resena.py: Escribir reseña.
- pagina_de_la_resena.py: Página de la reseña y los botones de cada reseña («me gusta», comentar y denunciar).
- lista_de_deseos.py: Lista de deseos y el botón «Lo quiero».
- repasar_la_biblioteca.py: Repasar la biblioteca (los juegos que las importaciones no saben si terminaste).
- que_jugar_despues.py: Qué jugar después (lo que te puede gustar de tu pendiente y del catálogo).
- comentarios_y_denuncias.py: los comentarios y las denuncias, que comparten las reseñas, las publicaciones y las listas.

La dirección de la Página de la reseña está en config/urls.py (u/<usuario>/resenas/<id>/). Aquí se reexportan las
vistas y lo que usan otras apps, para que urls.py y quien importa de library.views no cambien.
"""

from .comentarios_y_denuncias import _report, comment_thread
from .escribir_resena import review_delete, review_edit, review_new, review_preview
from .ficha_del_juego import mark_liked, rate, remove, review_context, set_status
from .lista_de_deseos import wish_target, wish_toggle, wishlist
from .que_jugar_despues import next_games, not_interested
from .repasar_la_biblioteca import tidy, tidy_answer
from .pagina_de_la_resena import (
    comment_delete, comment_report, review_comment, review_detail, review_like, review_report,
)
