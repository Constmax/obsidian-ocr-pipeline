// Apple Vision OCR of page images, for bench/apple_vision.py.
//
// Build: swiftc -O -parse-as-library bench/apple_vision.swift -o <scratch>/apple_vision
//        (macOS 26 SDK)
// Usage: apple_vision REQUESTS OUT_DIR PAGE.png...
//        REQUESTS is "text", "documents" or "text,documents"
//
// Writes OUT_DIR/<page>.json per image with the requested readings of the page:
//   "text"      RecognizeTextRequest (macOS 15; accurate, de-DE, language
//               correction), lines in Vision's order, as ocrmypdf-appleocr
//               uses them;
//   "documents" RecognizeDocumentsRequest (macOS 26), the document's text in
//               Vision's reading order plus its paragraphs, lists and tables.
// Seconds are wall time per request; the first image also pays model loading.
import Foundation
import Vision

let german = Locale.Language(identifier: "de-DE")

func fail(_ message: String) -> Never {
    FileHandle.standardError.write("\(message)\n".data(using: .utf8)!)
    exit(2)
}

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

func readText(_ url: URL) async throws -> [String: Any] {
    var request = RecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = [german]
    request.usesLanguageCorrection = true
    let start = ContinuousClock.now
    let observations = try await request.perform(on: url)
    return ["seconds": seconds(start), "lines": lines(observations)]
}

@available(macOS 26.0, *)
func readDocuments(_ url: URL) async throws -> [String: Any] {
    var request = RecognizeDocumentsRequest()
    request.textRecognitionOptions.recognitionLanguages = [german]
    request.textRecognitionOptions.useLanguageCorrection = true
    let start = ContinuousClock.now
    let observations = try await request.perform(on: url)
    let elapsed = seconds(start)
    let containers = observations.map { observation -> [String: Any] in
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
    return ["seconds": elapsed, "documents": containers]
}

@main
struct AppleVision {
    static func main() async throws {
        let arguments = CommandLine.arguments
        guard arguments.count >= 4 else {
            fail("usage: apple_vision text|documents|text,documents OUT_DIR PAGE.png...")
        }
        let requests = Set(arguments[1].split(separator: ",").map(String.init))
        guard !requests.isEmpty, requests.isSubset(of: ["text", "documents"]) else {
            fail("unknown request in \(arguments[1]); use text, documents or both")
        }
        if requests.contains("documents") {
            guard #available(macOS 26.0, *) else {
                fail("documents needs RecognizeDocumentsRequest (macOS 26)")
            }
        }
        let output = URL(fileURLWithPath: arguments[2])
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)

        for path in arguments.dropFirst(3) {
            let url = URL(fileURLWithPath: path)
            let page = url.deletingPathExtension().lastPathComponent
            var record: [String: Any] = ["page": page]
            var summary = [page]

            if requests.contains("text") {
                let text = try await readText(url)
                record["text"] = text
                summary.append(String(format: "text %.2f s (%d lines)", text["seconds"] as! Double,
                                      (text["lines"] as! [Any]).count))
            }
            if requests.contains("documents") {
                if #available(macOS 26.0, *) {
                    let documents = try await readDocuments(url)
                    record["documents"] = documents
                    let paragraphs = (documents["documents"] as! [[String: Any]]).reduce(0) {
                        $0 + (($1["paragraphs"] as? [Any])?.count ?? 0)
                    }
                    summary.append(String(format: "documents %.2f s (%d paragraphs)",
                                          documents["seconds"] as! Double, paragraphs))
                }
            }
            let data = try JSONSerialization.data(withJSONObject: record, options: [.prettyPrinted])
            try data.write(to: output.appendingPathComponent("\(page).json"))
            print(summary.joined(separator: ", "))
        }
    }
}
