import Foundation
import Capacitor
import CoreVideo
import ImageIO
import Vision

/// On-device background removal for the iPhone app: Apple's Vision framework
/// (VNGenerateForegroundInstanceMaskRequest, iOS 17+) answers how opaque every
/// pixel of a photo is, and this hands that matte back as a greyscale PNG.
///
/// Only the MATTE leaves here. The server composites it onto the seller's
/// colour with the same code every other cutout goes through, so a photo cut
/// on this phone looks like one cut anywhere else.
///
/// EVERY INSTANCE. Vision separates the foreground into instances -- the two
/// shoes of a pair, each card in a lot -- and a cutout that kept only the
/// first would delete the rest of the item. The mask asked for is the union
/// of `allInstances`.
///
/// SOFT. The scaled mask is a float confidence per pixel at the photo's own
/// resolution; it is quantised to 8 bits and never thresholded, so hair, fur,
/// lace and the edge of a glass keep their partial opacity.
///
/// ORIENTATION. The app hands over the bytes the server's /api/image/file
/// answers, which are upright and carry no EXIF orientation; whatever
/// orientation a file does carry is still passed to Vision, so the matte is in
/// the same frame as the pixels the server will composite.
///
/// The request runs on the Neural Engine / GPU and is not supported in the
/// Simulator ("unsupported compute device"), so the Simulator reports itself
/// unsupported rather than failing every photo.
@objc(ThryftCutoutPlugin)
public class ThryftCutoutPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "ThryftCutoutPlugin"
    public let jsName = "ThryftCutout"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "isSupported", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "prepare", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "segment", returnType: CAPPluginReturnPromise)
    ]

    private static let engine = "apple-vision"
    /// The server never sends more than 1600px; anything far past that is not
    /// a listing photo and would only cost memory.
    private static let maxSide = 4096
    /// One photo at a time: the queue on the JS side is serial too, and two
    /// concurrent Vision requests on a phone only slow each other down.
    private let queue = DispatchQueue(label: "com.thryftshop.cutout", qos: .userInitiated)

    struct CutoutError: Error {
        let message: String
        let code: String
    }

    // MARK: - capability

    private static func support() -> [String: Any] {
        #if targetEnvironment(simulator)
        return ["supported": false, "ready": false, "reason": "simulator", "engine": engine]
        #else
        if #available(iOS 17.0, *) {
            // The model ships with the OS: there is nothing to download.
            return ["supported": true, "ready": true, "engine": engine]
        }
        return ["supported": false, "ready": false, "reason": "os_version", "engine": engine]
        #endif
    }

    @objc func isSupported(_ call: CAPPluginCall) {
        call.resolve(Self.support())
    }

    @objc func prepare(_ call: CAPPluginCall) {
        call.resolve(Self.support())
    }

    // MARK: - the matte

    @objc func segment(_ call: CAPPluginCall) {
        guard let raw = call.getString("image"),
              let data = Data(base64Encoded: Self.stripDataURL(raw), options: .ignoreUnknownCharacters),
              !data.isEmpty else {
            call.reject("No photo was handed over.", "bad_input")
            return
        }
        #if targetEnvironment(simulator)
        call.reject("Background removal on the device doesn't run in the Simulator.", "unsupported")
        #else
        guard #available(iOS 17.0, *) else {
            call.reject("Background removal on this iPhone needs iOS 17 or later.", "unsupported")
            return
        }
        queue.async {
            autoreleasepool {
                do {
                    call.resolve(try Self.matte(for: data))
                } catch let error as CutoutError {
                    call.reject(error.message, error.code)
                } catch {
                    call.reject("The cutout failed on this device.", "inference_failed", error)
                }
            }
        }
        #endif
    }

    @available(iOS 17.0, *)
    private static func matte(for data: Data) throws -> [String: Any] {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
            throw CutoutError(message: "That photo couldn't be read.", code: "bad_input")
        }
        guard max(image.width, image.height) <= maxSide else {
            throw CutoutError(message: "That photo is too large to cut on this device.", code: "too_large")
        }
        let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
        let rawOrientation = (properties?[kCGImagePropertyOrientation] as? UInt32) ?? 1
        let orientation = CGImagePropertyOrientation(rawValue: rawOrientation) ?? .up

        let handler = VNImageRequestHandler(cgImage: image, orientation: orientation, options: [:])
        let request = VNGenerateForegroundInstanceMaskRequest()
        try handler.perform([request])
        guard let observation = request.results?.first, !observation.allInstances.isEmpty else {
            throw CutoutError(message: "No item was found in this photo.", code: "nothing_found")
        }
        let mask = try observation.generateScaledMaskForImage(
            forInstances: observation.allInstances, from: handler)
        let width = CVPixelBufferGetWidth(mask)
        let height = CVPixelBufferGetHeight(mask)
        let png = try greyscalePNG(from: mask)
        return [
            "mask": png.base64EncodedString(),
            "width": width,
            "height": height,
            "engine": engine
        ]
    }

    /// A one-channel mask buffer (float confidence, or 8-bit) as a greyscale PNG.
    private static func greyscalePNG(from buffer: CVPixelBuffer) throws -> Data {
        CVPixelBufferLockBaseAddress(buffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(buffer) else {
            throw CutoutError(message: "The cutout came back empty.", code: "inference_failed")
        }
        let width = CVPixelBufferGetWidth(buffer)
        let height = CVPixelBufferGetHeight(buffer)
        let rowBytes = CVPixelBufferGetBytesPerRow(buffer)
        var grey = [UInt8](repeating: 0, count: width * height)
        switch CVPixelBufferGetPixelFormatType(buffer) {
        case kCVPixelFormatType_OneComponent32Float:
            for y in 0..<height {
                let row = base.advanced(by: y * rowBytes).assumingMemoryBound(to: Float32.self)
                for x in 0..<width {
                    let v = (row[x] * 255).rounded()
                    grey[y * width + x] = UInt8(max(0, min(255, v)))
                }
            }
        case kCVPixelFormatType_OneComponent8:
            for y in 0..<height {
                let row = base.advanced(by: y * rowBytes).assumingMemoryBound(to: UInt8.self)
                for x in 0..<width {
                    grey[y * width + x] = row[x]
                }
            }
        default:
            throw CutoutError(message: "The cutout came back in a form this app can't read.",
                              code: "inference_failed")
        }
        guard let provider = CGDataProvider(data: Data(grey) as CFData),
              let image = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 8,
                                  bytesPerRow: width, space: CGColorSpaceCreateDeviceGray(),
                                  bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
                                  provider: provider, decode: nil, shouldInterpolate: false,
                                  intent: .defaultIntent) else {
            throw CutoutError(message: "The cutout couldn't be encoded.", code: "inference_failed")
        }
        let out = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(out, "public.png" as CFString, 1, nil) else {
            throw CutoutError(message: "The cutout couldn't be encoded.", code: "inference_failed")
        }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else {
            throw CutoutError(message: "The cutout couldn't be encoded.", code: "inference_failed")
        }
        return out as Data
    }

    private static func stripDataURL(_ value: String) -> String {
        if let comma = value.firstIndex(of: ","), value.hasPrefix("data:") {
            return String(value[value.index(after: comma)...])
        }
        return value
    }
}
