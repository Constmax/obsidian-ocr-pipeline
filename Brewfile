# Homebrew-Abhängigkeiten der obsidian-ocr-pipeline (Stufe 1 + 2)
# Installiert via: brew bundle --file=Brewfile
#
# Bewusst NICHT enthalten:
#  - ocrmypdf: brew-Build hat auf macOS den pyexpat-Bug — stattdessen venv-Weg
#    in setup.sh (~/.venvs/ocrmypdf)
#  - node: Plugin wird ohne Build installiert (main.js ist eingecheckt)
#  - python@3.12: brew-Bottles linken gegen ein neueres libexpat als macOS
#    mitbringt (pyexpat: Symbol not found). setup.sh nutzt stattdessen uv
#    (python-build-standalone, bundelt expat).

brew "qpdf"
brew "ghostscript"
brew "img2pdf"
brew "tesseract-lang"
brew "poppler"          # pdfinfo, pdftotext
brew "pngquant"
brew "jbig2enc"         # stellt das Kommando jbig2 bereit
brew "unpaper"
brew "uv"               # verwalteter Python 3.12 (Standalone-Build)
