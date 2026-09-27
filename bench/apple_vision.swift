// Apple Vision OCR of page images, for bench/apple_vision.py.
//
// Build: swiftc -O bench/apple_vision.swift -o <scratch>/apple_vision (macOS 26)
// Usage: apple_vision OUT_DIR PAGE.png...
//
// Writes OUT_DIR/<page>.json per image with two readings of the same page:
//   "text"      RecognizeTextRequest (accurate, de-DE, language correction),
//               lines in Vision's order, as ocrmypdf-appleocr uses them;
//   "documents" RecognizeDocumentsRequest (macOS 26), the document's text in
//               Vision's reading order plus its paragraphs, lists and tables.
// Seconds are wall time per request; the first image also pays model loading.
import Foundation
import Vision

func box(_ region: NormalizedRegion) -> [Double] {
    let rect = region.boundingBox
    return [rect.origin.x, rect.origin.y, rect.width, rect.height].map { Double($0) }
}

func lines(_ observations: [RecognizedTextObservation]) -> [[String: Any]] {
    observations.map { line in
        let top = line.topCandidates(1).first
        return ["text": top?.string ?? "", "confidence": Double(top?.confidence ?? 0),
                "box": box(line.boundingRegion)]
    }
}

func seconds(_ start: ContinuousClock.Instant) -> Double {
    let elapsed = ContinuousClock.now - start
    return Double(elapsed.components.seconds) + Double(elapsed.components.attoseconds) / 1e18
}

@main
struct AppleVision {
    static func main() async throws {
        let arguments = CommandLine.arguments
        guard arguments.count >= 3 else {
            FileHandle.standardError.write("usage: apple_vision OUT_DIR PAGE.png...\n".data(using: .utf8)!)
            exit(2)
        }
        let output = URL(fileURLWithPath: arguments[1])
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        let german = Locale.Language(identifier: "de-DE")

        var textRequest = RecognizeTextRequest()
        textRequest.recognitionLevel = .accurate
        textRequest.recognitionLanguages = [german]
        textRequest.usesLanguageCorrection = true

        var documentsRequest = RecognizeDocumentsRequest()
        documentsRequest.textRecognitionOptions.recognitionLanguages = [german]
        documentsRequest.textRecognitionOptions.useLanguageCorrection = true

        for path in arguments.dropFirst(2) {
            let url = URL(fileURLWithPath: path)
            let page = url.deletingPathExtension().lastPathComponent

            var start = ContinuousClock.now
            let textLines = try await textRequest.perform(on: url)
            let textSeconds = seconds(start)

            start = ContinuousClock.now
            let documents = try await documentsRequest.perform(on: url)
            let documentsSeconds = seconds(start)

            let containers = documents.map { observation -> [String: Any] in
                let document = observation.document
                return [
                    "title": document.title?.transcript as Any,
                    "transcript": document.text.transcript,
                    "lines": lines(document.text.lines),
                    "paragraphs": document.paragraphs.map {
                        ["transcript": $0.transcript, "box": box($0.boundingRegion)] as [String: Any]
                    },
                    "lists": document.lists.count,
                    "tables": document.tables.count,
                ]
            }
            let record: [String: Any] = [
                "page": page,
                "text": ["seconds": textSeconds, "lines": lines(textLines)],
                "documents": ["seconds": documentsSeconds, "documents": containers],
            ]
            let data = try JSONSerialization.data(withJSONObject: record, options: [.prettyPrinted])
            try data.write(to: output.appendingPathComponent("\(page).json"))
            print(String(format: "%@ text %.2f s (%d lines), documents %.2f s (%d paragraphs)",
                         page, textSeconds, textLines.count, documentsSeconds,
                         containers.reduce(0) { $0 + (($1["paragraphs"] as? [Any])?.count ?? 0) }))
        }
    }
}
