// Apple Vision OCR (spike 0a, method D). usage: applevision <image> [--fast]
// Prints recognized lines top to bottom.
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
try VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
let obs = (req.results ?? []).sorted { $0.boundingBox.maxY > $1.boundingBox.maxY }
for o in obs { if let t = o.topCandidates(1).first { print(t.string) } }
