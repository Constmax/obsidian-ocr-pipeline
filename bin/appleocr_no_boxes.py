"""OCRmyPDF plugin: the Apple Vision text layer without its red line boxes.

ocrmypdf-appleocr 0.3.4 (and 0.4.0) calls its renderer with `boxes=True`, so
every line gets a stroked red rectangle under the scan; viewers show it while
the image is still decoding (issue #209). Load this file with
`--plugin ocrmypdf_appleocr --plugin <this file>`: it reroutes that call with
`boxes=False` and leaves everything else to the engine.
"""
import inspect

import ocrmypdf_appleocr

_generate_pdf = ocrmypdf_appleocr.generate_pdf
# A renamed parameter would make the rebinding below a silent no-op.
if "boxes" not in inspect.signature(_generate_pdf).parameters:
    raise ImportError("ocrmypdf_appleocr.generate_pdf has no `boxes` parameter; see issue #209")


def _generate_pdf_without_boxes(*args, **kwargs):
    call = inspect.signature(_generate_pdf).bind(*args, **kwargs)
    call.arguments["boxes"] = False
    return _generate_pdf(*call.args, **call.kwargs)


ocrmypdf_appleocr.generate_pdf = _generate_pdf_without_boxes
