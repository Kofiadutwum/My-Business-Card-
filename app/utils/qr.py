"""QR generation.

Rendered server-side with segno into an SVG string that is dropped straight
into the template. No JavaScript library, no external QR service, and no
image file to write to disk or clean up later.

Error correction is set to 'h' (roughly 30% recoverable) because these codes
get printed on badges and shopfront stickers, where a scratch is normal.
"""

import io

import segno


def qr_svg(data, scale=6, dark="#14161a", light=None):
    # segno writes SVG as encoded bytes, not text, so the buffer is BytesIO
    # and the result is decoded before it reaches the template.
    qr = segno.make(data, error="h")
    buffer = io.BytesIO()
    qr.save(
        buffer,
        kind="svg",
        scale=scale,
        dark=dark,
        light=light,
        border=2,
        xmldecl=False,
        svgns=True,
        svgclass="qr",
        lineclass="qr-line",
    )
    return buffer.getvalue().decode("utf-8")


def qr_png_bytes(data, scale=10):
    qr = segno.make(data, error="h")
    buffer = io.BytesIO()
    qr.save(buffer, kind="png", scale=scale, dark="#14161a", light="#ffffff", border=2)
    return buffer.getvalue()
