// Listens to audio files with macOS's built-in sound classifier (SoundAnalysis, no download, runs on the
// Neural Engine) and prints, per file, how confident it is in each drum-related sound for every few seconds.
// Read-only: files are only opened for reading.
//
//   sound-classifier [--window SECONDS] FILE...   one JSON line per file
//   sound-classifier --self-check                 {"ok": true, ...} when the classifier loads
//
// Built by scripts/build_app.py (and on first use from source) with: swiftc -O sound_classifier.swift
import Foundation
import SoundAnalysis

let classes = ["drum_kit", "drum", "snare_drum", "hi_hat", "bass_drum", "cymbal", "percussion", "clapping",
               "mallet_percussion", "music", "rapping", "singing", "speech"]

final class Collector: NSObject, SNResultsObserving {
    var windows: [[Double]] = []
    var failure: String?

    func request(_ request: SNRequest, didProduce result: SNResult) {
        guard let result = result as? SNClassificationResult else { return }
        windows.append(classes.map { result.classification(forIdentifier: $0)?.confidence ?? 0 })
    }

    func request(_ request: SNRequest, didFailWithError error: Error) {
        failure = error.localizedDescription
    }
}

func emit(_ object: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}

var arguments = Array(CommandLine.arguments.dropFirst())
var window = 3.0
if arguments.first == "--self-check" {
    do {
        let request = try SNClassifySoundRequest(classifierIdentifier: .version1)
        emit(["ok": true, "known": request.knownClassifications.count, "classes": classes])
        exit(0)
    } catch {
        emit(["ok": false, "error": error.localizedDescription])
        exit(1)
    }
}
if arguments.first == "--window", arguments.count > 1, let seconds = Double(arguments[1]), seconds > 0 {
    window = seconds
    arguments.removeFirst(2)
}
for path in arguments {
    do {
        let analyzer = try SNAudioFileAnalyzer(url: URL(fileURLWithPath: path))
        let request = try SNClassifySoundRequest(classifierIdentifier: .version1)
        request.windowDuration = CMTimeMakeWithSeconds(window, preferredTimescale: 48_000)
        request.overlapFactor = 0
        let collector = Collector()
        try analyzer.add(request, withObserver: collector)
        analyzer.analyze()  // blocks until the whole file is read
        if let failure = collector.failure {
            emit(["path": path, "error": failure])
        } else {
            emit(["path": path, "window": window, "classes": classes, "windows": collector.windows])
        }
    } catch {
        emit(["path": path, "error": error.localizedDescription])
    }
}
