#!/usr/bin/env python3
"""Rasterise the PaintOmics AI brand mark for use in outgoing e-mail.

E-mail is not the web. Gmail and every Outlook build refuse to render an
``<img>`` whose source is an SVG, so ``paintomics-mark.svg`` -- the mark the
application itself uses -- cannot be linked from a message. The mark has to
ship as a PNG, and this script is how that PNG is produced so the raster is
never hand-edited away from the vector it came from.

Rendered at 3x the display size (60 CSS px in the mail template) because
e-mail has no ``srcset``: one file has to serve a Retina phone and a 96 dpi
desktop alike, and the only lever is to send more pixels than are needed and
let ``width``/``height`` scale them down.

The background stays transparent. The connector ring is #A6ABB2 and the node
strokes are white, which reads correctly against both the light card the
template paints and the dark surface Apple Mail substitutes when it inverts.

Run after any edit to paintomics-mark.svg:

    python resources/images/build-email-mark.py
"""

import os

import cairosvg

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "paintomics-mark.svg")
TARGET = os.path.join(HERE, "paintomics-mark-email.png")

# 60 CSS px in the template, tripled. Anything less is visibly soft on a phone.
DISPLAY_PX = 60
SCALE = 3


def main():
    cairosvg.svg2png(
        url=SOURCE,
        write_to=TARGET,
        output_width=DISPLAY_PX * SCALE,
        output_height=DISPLAY_PX * SCALE,
    )
    print("wrote %s (%dx%d)" % (TARGET, DISPLAY_PX * SCALE, DISPLAY_PX * SCALE))


if __name__ == "__main__":
    main()
