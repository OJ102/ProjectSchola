// Apple Vision OCR for scanned pages (decision 20: the free default for
// scanned prose on macOS). usage: applevision <image> [--fast]
// Prints one JSON object per recognized line, top to bottom:
//   {"t": text, "x": left, "y": top, "w": width, "h": height, "c": confidence}
// Coordinates are fractions of the image size with the origin at the top left.
import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count > 1, let img = NSImage(contentsOfFile: args[1]),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("cannot read image\n".data(using: .utf8)!); exit(2)
}
let req = VNRecognizeTextRequest()
req.recognitionLevel = args.contains("--fast") ? .fast : .accurate
req.usesLanguageCorrection = true
if #available(macOS 13.0, *) { req.automaticallyDetectsLanguage = true }
do {
    try VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
} catch {
    FileHandle.standardError.write("vision request failed\n".data(using: .utf8)!); exit(3)
}
let obs = (req.results ?? []).sorted { $0.boundingBox.maxY > $1.boundingBox.maxY }
for o in obs {
    guard let t = o.topCandidates(1).first else { continue }
    let b = o.boundingBox
    let rec: [String: Any] = ["t": t.string, "x": b.minX, "y": 1.0 - b.maxY, "w": b.width, "h": b.height,
                              "c": t.confidence]
    if let d = try? JSONSerialization.data(withJSONObject: rec), let s = String(data: d, encoding: .utf8) {
        print(s)
    }
}
