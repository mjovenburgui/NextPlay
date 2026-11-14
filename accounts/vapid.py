"""Claves y firma VAPID (RFC 8292) de los avisos push, solo con la biblioteca estándar de Python: ECDSA sobre la curva
P-256 con SHA-256 y el número de un solo uso determinista de RFC 6979, que es lo que pide el JWT «ES256» de VAPID.

Solo hace falta firmar: los avisos viajan sin contenido (accounts.push), así que no hay nada que cifrar. Las claves van
en base64url, como las usan los navegadores y pywebpush: la pública, el punto sin comprimir (65 bytes, la
applicationServerKey de los navegadores); la privada, el número de 32 bytes. Las genera «manage.py vapid_keys». Los
tests comprueban la firma con los vectores de RFC 6979.

Pantallas: ninguna (firma los avisos que llegan al móvil o al ordenador, ver accounts.push).
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from functools import lru_cache
from urllib.parse import urlsplit

# La curva P-256 (secp256r1, FIPS 186-4): y² = x³ + A·x + B sobre el primo P, con el punto base G, de orden N.
P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
G = (
    0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
    0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
)
# Lo que dura cada firma: los servicios push no aceptan más de 24 horas.
TOKEN_LIFETIME = 12 * 3600


def b64url(data):
    """data (bytes) en base64url sin el relleno «=», como van las claves y los trozos de un JWT.

    Pantallas: ninguna (claves VAPID y firma de los avisos push).
    """
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text):
    """Lo contrario de b64url: los bytes de text, con o sin relleno.

    Pantallas: ninguna (claves VAPID y firma de los avisos push).
    """
    text = (text or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _add(first, second):
    """Suma de dos puntos de la curva en coordenadas afines (None es el punto del infinito).

    Pantallas: ninguna (claves VAPID y firma de los avisos push).
    """
    if first is None:
        return second
    if second is None:
        return first
    x1, y1 = first
    x2, y2 = second
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        slope = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        slope = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (slope * slope - x1 - x2) % P
    return x3, (slope * (x1 - x3) - y1) % P


def _multiply(scalar, point=G):
    """scalar veces point (por defecto, el punto base), sumando y doblando.

    Pantallas: ninguna (claves VAPID y firma de los avisos push).
    """
    result, addend = None, point
    while scalar:
        if scalar & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        scalar >>= 1
    return result


def public_key(private):
    """La clave pública (el punto private·G sin comprimir: 0x04, x e y, 65 bytes) de la privada private (un número).

    Pantallas: ninguna (claves VAPID de los avisos push).
    """
    x, y = _multiply(private)
    return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


def generate_keys():
    """Un par de claves nuevo: (pública, privada), las dos en base64url, para VAPID_PUBLIC_KEY y VAPID_PRIVATE_KEY.

    Pantallas: ninguna (las genera «manage.py vapid_keys»).
    """
    private = secrets.randbelow(N - 1) + 1
    return b64url(public_key(private)), b64url(private.to_bytes(32, "big"))


@lru_cache(maxsize=8)
def valid_keys(public, private):
    """Si public y private (base64url) son un par de claves de P-256 que van juntas. Se calcula una vez por par.

    Pantallas: Editar perfil (Avisos en el móvil y el ordenador: si se pueden activar).
    """
    try:
        number = int.from_bytes(b64url_decode(private), "big")
        return len(b64url_decode(private)) == 32 and 1 <= number < N and public_key(number) == b64url_decode(public)
    except ValueError:
        return False


def _nonce(private, digest):
    """El número de un solo uso k de la firma, determinista (RFC 6979, sección 3.2), para SHA-256 y P-256: sale de la
    clave privada y del resumen del mensaje, así que nunca se repite para dos mensajes distintos ni depende del azar.

    Pantallas: ninguna (firma de los avisos push).
    """
    key = private.to_bytes(32, "big")
    hashed = (int.from_bytes(digest, "big") % N).to_bytes(32, "big")
    v, k = b"\x01" * 32, b"\x00" * 32
    k = hmac.new(k, v + b"\x00" + key + hashed, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\x01" + key + hashed, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        v = hmac.new(k, v, hashlib.sha256).digest()
        candidate = int.from_bytes(v, "big")
        if 1 <= candidate < N:
            return candidate
        k = hmac.new(k, v + b"\x00", hashlib.sha256).digest()
        v = hmac.new(k, v, hashlib.sha256).digest()


def sign(message, private):
    """Firma ECDSA P-256 con SHA-256 de message (bytes) con la clave privada private (un número): r y s seguidos, 32
    bytes cada uno, como la firma de un JWT ES256.

    Pantallas: ninguna (firma de los avisos push).
    """
    digest = hashlib.sha256(message).digest()
    k = _nonce(private, digest)
    r = _multiply(k)[0] % N
    s = pow(k, -1, N) * (int.from_bytes(digest, "big") + r * private) % N
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def verify(message, signature, public):
    """Si signature (r y s, 64 bytes) es una firma válida de message con la clave pública public (65 bytes). La usan los
    tests para comprobar las firmas.

    Pantallas: ninguna (firma de los avisos push).
    """
    if len(signature) != 64 or len(public) != 65 or public[0] != 4:
        return False
    r, s = int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
    if not (1 <= r < N and 1 <= s < N):
        return False
    point = (int.from_bytes(public[1:33], "big"), int.from_bytes(public[33:], "big"))
    w = pow(s, -1, N)
    z = int.from_bytes(hashlib.sha256(message).digest(), "big")
    result = _add(_multiply(z * w % N), _multiply(r * w % N, point))
    return result is not None and result[0] % N == r


def token(audience, subject, private_key, now=None):
    """El JWT de VAPID (ES256) para mandar avisos al servicio push audience (su origen, «https://host»), con subject
    (un mailto: o una web https de quien manda) y la clave privada private_key en base64url. Caduca en TOKEN_LIFETIME.

    Pantallas: ninguna (firma de los avisos push).
    """
    now = int(time.time() if now is None else now)
    header = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = {"aud": audience, "exp": now + TOKEN_LIFETIME, "sub": subject}
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode("ascii")
    signature = sign(signing_input, int.from_bytes(b64url_decode(private_key), "big"))
    return f"{header}.{body}.{b64url(signature)}"


def authorization(endpoint, subject, public_key_b64, private_key_b64):
    """La cabecera Authorization de un aviso push a endpoint (RFC 8292): «vapid t=<JWT>, k=<clave pública>».

    Pantallas: ninguna (firma de los avisos push).
    """
    parts = urlsplit(endpoint)
    audience = f"{parts.scheme}://{parts.netloc}"
    return f"vapid t={token(audience, subject, private_key_b64)}, k={public_key_b64}"
