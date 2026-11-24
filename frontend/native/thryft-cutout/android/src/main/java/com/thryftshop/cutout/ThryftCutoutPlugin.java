package com.thryftshop.cutout;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Build;
import android.util.Base64;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.google.android.gms.common.ConnectionResult;
import com.google.android.gms.common.GoogleApiAvailability;
import com.google.android.gms.common.moduleinstall.InstallStatusListener;
import com.google.android.gms.common.moduleinstall.ModuleInstall;
import com.google.android.gms.common.moduleinstall.ModuleInstallClient;
import com.google.android.gms.common.moduleinstall.ModuleInstallRequest;
import com.google.android.gms.common.moduleinstall.ModuleInstallStatusUpdate;
import com.google.android.gms.tasks.Tasks;
import com.google.mlkit.common.MlKitException;
import com.google.mlkit.vision.common.InputImage;
import com.google.mlkit.vision.segmentation.subject.SubjectSegmentation;
import com.google.mlkit.vision.segmentation.subject.SubjectSegmentationResult;
import com.google.mlkit.vision.segmentation.subject.SubjectSegmenter;
import com.google.mlkit.vision.segmentation.subject.SubjectSegmenterOptions;

import java.io.ByteArrayOutputStream;
import java.nio.FloatBuffer;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * On-device background removal for the Android app: Google ML Kit's subject
 * segmentation answers how sure it is that each pixel of a photo belongs to
 * the foreground, and this hands that confidence back as a greyscale PNG.
 *
 * Only the MATTE leaves here. The server composites it onto the seller's
 * colour with the same code every other cutout goes through.
 *
 * The FOREGROUND confidence mask is used, not a per-subject one, so every
 * subject in the photo is kept -- both shoes of a pair, every card in a lot.
 * It is quantised to 8 bits and never thresholded: soft edges stay soft.
 *
 * The model is not in this app. Google Play services downloads it (at
 * install, per the manifest's DEPENDENCIES entry, or on demand through
 * prepare()), so a phone without Play services, or one whose download has not
 * finished, reports that plainly and the app falls back rather than failing
 * every photo.
 *
 * Written in Java rather than Kotlin so the plugin adds no Kotlin toolchain
 * the generated app project would have to line up with.
 */
@CapacitorPlugin(name = "ThryftCutout")
public class ThryftCutoutPlugin extends Plugin {

    private static final String ENGINE = "mlkit";
    /** The server never sends more than 1600px. */
    private static final int MAX_SIDE = 4096;
    private static final long SEGMENT_TIMEOUT_S = 60;

    /** One photo at a time, off the main thread. */
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private SubjectSegmenter segmenter;

    private synchronized SubjectSegmenter segmenter() {
        if (segmenter == null) {
            segmenter = SubjectSegmentation.getClient(
                new SubjectSegmenterOptions.Builder()
                    .enableForegroundConfidenceMask()
                    .build());
        }
        return segmenter;
    }

    private JSObject support(boolean supported, boolean ready, String reason) {
        JSObject out = new JSObject();
        out.put("supported", supported);
        out.put("ready", ready);
        out.put("engine", ENGINE);
        if (reason != null) {
            out.put("reason", reason);
        }
        return out;
    }

    /** Null when Play services can host the model, else why not. */
    private String playServicesProblem() {
        if (Build.VERSION.SDK_INT < 24) {
            return "os_version";
        }
        int status = GoogleApiAvailability.getInstance().isGooglePlayServicesAvailable(getContext());
        return status == ConnectionResult.SUCCESS ? null : "play_services";
    }

    @PluginMethod
    public void isSupported(PluginCall call) {
        String problem = playServicesProblem();
        if (problem != null) {
            call.resolve(support(false, false, problem));
            return;
        }
        ModuleInstall.getClient(getContext())
            .areModulesAvailable(segmenter())
            .addOnSuccessListener(response -> call.resolve(
                support(true, response.areModulesAvailable(),
                        response.areModulesAvailable() ? null : "model_not_downloaded")))
            .addOnFailureListener(e -> call.resolve(support(true, false, "model_check_failed")));
    }

    /**
     * Make sure the segmentation model is on the phone, downloading it through
     * Play services if it is not. Emits "downloadProgress" ({progress: 0-1})
     * while it downloads and resolves with the same shape as isSupported once
     * it is there (or could not be had).
     */
    @PluginMethod
    public void prepare(PluginCall call) {
        String problem = playServicesProblem();
        if (problem != null) {
            call.resolve(support(false, false, problem));
            return;
        }
        ModuleInstallClient client = ModuleInstall.getClient(getContext());
        final InstallStatusListener[] holder = new InstallStatusListener[1];
        holder[0] = update -> {
            ModuleInstallStatusUpdate.ProgressInfo info = update.getProgressInfo();
            if (info != null && info.getTotalBytesToDownload() > 0) {
                JSObject progress = new JSObject();
                progress.put("progress",
                    (double) info.getBytesDownloaded() / (double) info.getTotalBytesToDownload());
                notifyListeners("downloadProgress", progress);
            }
            int state = update.getInstallState();
            if (state == ModuleInstallStatusUpdate.InstallState.STATE_COMPLETED) {
                client.unregisterListener(holder[0]);
                call.resolve(support(true, true, null));
            } else if (state == ModuleInstallStatusUpdate.InstallState.STATE_FAILED
                || state == ModuleInstallStatusUpdate.InstallState.STATE_CANCELED) {
                client.unregisterListener(holder[0]);
                call.resolve(support(true, false, "model_download_failed"));
            }
        };
        ModuleInstallRequest request = ModuleInstallRequest.newBuilder()
            .addApi(segmenter())
            .setListener(holder[0])
            .build();
        client.installModules(request)
            .addOnSuccessListener(response -> {
                if (response.areModulesAlreadyInstalled()) {
                    client.unregisterListener(holder[0]);
                    call.resolve(support(true, true, null));
                }
                // Otherwise the listener above resolves once it is done.
            })
            .addOnFailureListener(e -> {
                client.unregisterListener(holder[0]);
                call.resolve(support(true, false, "model_download_failed"));
            });
    }

    @PluginMethod
    public void segment(PluginCall call) {
        String raw = call.getString("image");
        if (raw == null || raw.isEmpty()) {
            call.reject("No photo was handed over.", "bad_input");
            return;
        }
        String problem = playServicesProblem();
        if (problem != null) {
            call.reject("Background removal on this phone needs Google Play services.", "unsupported");
            return;
        }
        executor.execute(() -> {
            Bitmap photo = null;
            Bitmap matte = null;
            try {
                byte[] bytes = Base64.decode(stripDataUrl(raw), Base64.DEFAULT);
                photo = BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
                if (photo == null) {
                    call.reject("That photo couldn't be read.", "bad_input");
                    return;
                }
                if (Math.max(photo.getWidth(), photo.getHeight()) > MAX_SIDE) {
                    call.reject("That photo is too large to cut on this phone.", "too_large");
                    return;
                }
                // The bytes the app hands over are upright with no EXIF
                // orientation (the server's /api/image/file), so 0 degrees.
                InputImage image = InputImage.fromBitmap(photo, 0);
                SubjectSegmentationResult result = Tasks.await(
                    segmenter().process(image), SEGMENT_TIMEOUT_S, TimeUnit.SECONDS);
                FloatBuffer confidence = result.getForegroundConfidenceMask();
                if (confidence == null) {
                    call.reject("No item was found in this photo.", "nothing_found");
                    return;
                }
                int width = photo.getWidth();
                int height = photo.getHeight();
                int[] pixels = new int[width * height];
                confidence.rewind();
                boolean any = false;
                for (int i = 0; i < pixels.length && confidence.hasRemaining(); i++) {
                    int v = Math.round(Math.max(0f, Math.min(1f, confidence.get())) * 255f);
                    any |= v > 0;
                    // Grey in RGB, fully opaque: the server reads a matte by
                    // its brightness when it carries no transparency.
                    pixels[i] = 0xFF000000 | (v << 16) | (v << 8) | v;
                }
                if (!any) {
                    call.reject("No item was found in this photo.", "nothing_found");
                    return;
                }
                matte = Bitmap.createBitmap(pixels, width, height, Bitmap.Config.ARGB_8888);
                ByteArrayOutputStream png = new ByteArrayOutputStream();
                matte.compress(Bitmap.CompressFormat.PNG, 100, png);
                JSObject out = new JSObject();
                out.put("mask", Base64.encodeToString(png.toByteArray(), Base64.NO_WRAP));
                out.put("width", width);
                out.put("height", height);
                out.put("engine", ENGINE);
                call.resolve(out);
            } catch (ExecutionException e) {
                Throwable cause = e.getCause();
                if (cause instanceof MlKitException
                    && ((MlKitException) cause).getErrorCode() == MlKitException.UNAVAILABLE) {
                    call.reject("The background remover is still downloading on this phone.",
                        "model_unavailable");
                } else {
                    call.reject("The cutout failed on this phone.", "inference_failed");
                }
            } catch (TimeoutException e) {
                call.reject("The cutout took too long on this phone.", "timeout");
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                call.reject("The cutout was interrupted.", "cancelled");
            } catch (OutOfMemoryError e) {
                call.reject("This phone ran out of memory cutting that photo.", "out_of_memory");
            } catch (Exception e) {
                call.reject("The cutout failed on this phone.", "inference_failed");
            } finally {
                if (photo != null) {
                    photo.recycle();
                }
                if (matte != null) {
                    matte.recycle();
                }
            }
        });
    }

    @Override
    protected void handleOnDestroy() {
        executor.shutdownNow();
        synchronized (this) {
            if (segmenter != null) {
                segmenter.close();
                segmenter = null;
            }
        }
        super.handleOnDestroy();
    }

    private static String stripDataUrl(String value) {
        if (value.startsWith("data:")) {
            int comma = value.indexOf(',');
            if (comma >= 0) {
                return value.substring(comma + 1);
            }
        }
        return value;
    }
}
