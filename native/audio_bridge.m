// DAW Multitool's audio bridge. Your DAW plays into the "DAW Multitool" audio device (native/audio_device); this
// helper hears it from the device's input side and plays it on to a real output (headphones, an interface's
// outputs 3-4…). It resamples by the device's speed, so a varispeed DAW comes out slower and lower, or faster and
// higher, like tape. It can record what you hear. DAW Multitool runs it (multitool/audio/bridge.py).
//
// Commands, one per line on stdin:
//   output <left channel from 0> <device UID>   where to play (no UID: the Mac's default output)
//   speed <0.5 to 2>                            the DAW's speed (the device's clock glides there)
//   record <path>                               record what you hear into a new 32-bit float WAV (never replaces)
//   stop-record
//   quit                                        (also when stdin closes; the DAW's speed goes back to 100%)
// It answers with JSON lines on stdout: "status" about 4 times a second, "recorded" and "error" events.
// Other modes: --list (the Mac's audio devices as JSON), --self-check, --test <folder> (no audio device is used).
#import <AVFoundation/AVFoundation.h>
#include <CoreAudio/CoreAudio.h>
#include <errno.h>
#include <fcntl.h>
#include <mach/mach_time.h>
#include <math.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define kDeviceUID          "com.dawmultitool.audio.device"
#define kTargetSpeed        'dmts'
#define kCurrentSpeed       'dmcs'
#define kRingFrames         (1u << 17)      // about 2.7 s at 48 kHz
#define kRecordRingFrames   (1u << 18)
#define kMaxBlock           4096
#define kMarginFrames       128             // extra latency kept so the output never runs dry
#define kTopSpeed           2.0
#define kCatchUpSeconds     1.0             // how quickly a latency error is worked off
#define kMaxCorrection      0.005           // at most 0.5% (8.6 cents) faster or slower while correcting
#define kFillSmoothing      0.02
#define kWavHeaderBytes     56
#define kMaxWavFrames       ((UINT32_MAX - 64) / 8)

// ---------------------------------------------------------------------------------------------- ring buffer
// One writer thread and one reader thread, no locks, so it's safe in audio callbacks.
typedef struct {
    float* frames;                  // stereo, interleaved
    uint32_t capacity;              // a power of two
    _Atomic uint64_t written, read;
} Ring;

static void ringInit(Ring* r, uint32_t capacity) {
    r->frames = calloc((size_t)capacity * 2, sizeof(float));
    r->capacity = capacity;
    atomic_init(&r->written, 0);
    atomic_init(&r->read, 0);
}

static uint32_t ringFill(Ring* r) {
    return (uint32_t)(atomic_load_explicit(&r->written, memory_order_acquire) - atomic_load_explicit(&r->read, memory_order_acquire));
}

static void ringCopy(Ring* r, uint64_t at, uint32_t count, const float* in, float* out) {
    uint32_t start = (uint32_t)(at & (r->capacity - 1));
    uint32_t first = count < r->capacity - start ? count : r->capacity - start;
    if (in) {
        memcpy(r->frames + start * 2, in, first * 2 * sizeof(float));
        memcpy(r->frames, in + first * 2, (count - first) * 2 * sizeof(float));
    } else {
        memcpy(out, r->frames + start * 2, first * 2 * sizeof(float));
        memcpy(out + first * 2, r->frames, (count - first) * 2 * sizeof(float));
    }
}

static uint32_t ringPush(Ring* r, const float* in, uint32_t count) {
    uint64_t written = atomic_load_explicit(&r->written, memory_order_relaxed);
    uint32_t space = r->capacity - (uint32_t)(written - atomic_load_explicit(&r->read, memory_order_acquire));
    if (count > space) count = space;
    ringCopy(r, written, count, in, NULL);
    atomic_store_explicit(&r->written, written + count, memory_order_release);
    return count;
}

// Takes up to `count` frames out (`out` NULL skips them).
static uint32_t ringPop(Ring* r, float* out, uint32_t count) {
    uint64_t read = atomic_load_explicit(&r->read, memory_order_relaxed);
    uint32_t fill = (uint32_t)(atomic_load_explicit(&r->written, memory_order_acquire) - read);
    if (count > fill) count = fill;
    if (out) ringCopy(r, read, count, NULL, out);
    atomic_store_explicit(&r->read, read + count, memory_order_release);
    return count;
}

// ---------------------------------------------------------------------------------------------- the bridge
typedef struct {
    Ring input;                     // what the DAW played, from the DAW Multitool device
    _Atomic double inRate, outRate, speed;
    _Atomic uint32_t inChunk;       // frames in the latest input callback
    uint64_t lastWritten;           // the input ring's write count at the previous render
    // the resampler: only the output callback touches these
    float history[4][2];            // the last four input frames; output lies between [1] and [2]
    double phase, fill, chunk;
    bool primed;
    _Atomic uint32_t dropouts;
    _Atomic float peak;
    // recording what you hear
    Ring record;
    _Atomic bool recording, stopWriter;
    FILE* file;
    char path[1024];
    double recordRate;
    uint64_t recorded;
    _Atomic bool writeFailed;
    pthread_t writer;
} Bridge;

static void bridgeInit(Bridge* b, double inRate, double outRate) {
    memset(b, 0, sizeof(*b));
    ringInit(&b->input, kRingFrames);
    ringInit(&b->record, kRecordRingFrames);
    atomic_init(&b->inRate, inRate);
    atomic_init(&b->outRate, outRate);
    atomic_init(&b->speed, 1.0);
}

static void bridgeInput(Bridge* b, const float* frames, uint32_t count) {
    ringPush(&b->input, frames, count);  // full only if the output stalled: rendering catches up
}

// Cubic (Hermite) interpolation at t between x0 and x1.
static float hermite(float xm1, float x0, float x1, float x2, float t) {
    float c1 = 0.5f * (x1 - xm1);
    float c2 = xm1 - 2.5f * x0 + 2.0f * x1 - 0.5f * x2;
    float c3 = 0.5f * (x2 - xm1) + 1.5f * (x0 - x1);
    return ((c3 * t + c2) * t + c1) * t + x0;
}

// Renders `frames` frames of what you hear (stereo, interleaved). Reads the input at inRate·speed/outRate input
// frames per output frame, nudged by up to 0.5% to hold the latency steady (the two devices' clocks never match).
static void bridgeRender(Bridge* b, float* out, uint32_t frames) {
    double inRate = atomic_load(&b->inRate), outRate = atomic_load(&b->outRate), speed = atomic_load(&b->speed);
    double base = inRate * speed / outRate;
    double latest = atomic_load_explicit(&b->inChunk, memory_order_relaxed);
    b->chunk = latest > b->chunk ? latest : b->chunk - (b->chunk - latest) * 0.001;  // the DAW's buffer size
    // The latency to hold, in input frames: one DAW buffer, what one output callback takes at top speed (so it
    // needn't grow during a glide), and a margin.
    double target = b->chunk + frames * inRate * kTopSpeed / outRate + kMarginFrames;
    double fill = ringFill(&b->input);
    uint64_t written = atomic_load_explicit(&b->input.written, memory_order_acquire);
    bool arrived = written != b->lastWritten;
    b->lastWritten = written;
    uint32_t done = 0;
    if (!b->primed) {
        // Start just after a DAW buffer arrived, the top of the fill's sawtooth: keeping half a cycle more than the
        // target there puts the average fill on the target, so there's nothing left to correct.
        double top = target + fmax(0.0, (b->chunk - frames * base) / 2);
        if (fill < top || !arrived) goto silence;
        ringPop(&b->input, NULL, (uint32_t)(fill - top));
        memset(b->history, 0, sizeof(b->history));
        b->phase = 0;
        fill = top;
        b->fill = target;
        b->primed = true;
    } else if (fill > target + inRate * 0.25) {  // a quarter second behind (the output stalled): catch up at once
        ringPop(&b->input, NULL, (uint32_t)(fill - target));
        fill = b->fill = target;
    }
    b->fill += (fill - b->fill) * kFillSmoothing;
    double correction = (b->fill - target) / (inRate * speed * kCatchUpSeconds);
    if (correction > kMaxCorrection) correction = kMaxCorrection;
    if (correction < -kMaxCorrection) correction = -kMaxCorrection;
    double step = base * (1.0 + correction);
    for (; done < frames; done++) {
        float t = (float)b->phase;
        for (int c = 0; c < 2; c++)
            out[done * 2 + c] = hermite(b->history[0][c], b->history[1][c], b->history[2][c], b->history[3][c], t);
        b->phase += step;
        while (b->phase >= 1.0) {
            float next[2];
            if (ringPop(&b->input, next, 1) == 0) {  // ran dry: wait for the input to build up again
                atomic_fetch_add(&b->dropouts, 1);
                b->primed = false;
                done++;
                goto silence;
            }
            memmove(b->history[0], b->history[1], 3 * sizeof(b->history[0]));
            b->history[3][0] = next[0];
            b->history[3][1] = next[1];
            b->phase -= 1.0;
        }
    }
silence:
    memset(out + done * 2, 0, (frames - done) * 2 * sizeof(float));
    float peak = 0;
    for (uint32_t i = 0; i < frames * 2; i++) peak = fmaxf(peak, fabsf(out[i]));
    if (peak > atomic_load_explicit(&b->peak, memory_order_relaxed)) atomic_store_explicit(&b->peak, peak, memory_order_relaxed);
    if (atomic_load_explicit(&b->recording, memory_order_acquire)) ringPush(&b->record, out, frames);
}

// ---------------------------------------------------------------------------------------------- recording
static void put32(uint8_t* p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
static void put16(uint8_t* p, uint16_t v) { p[0] = v; p[1] = v >> 8; }

// A 32-bit float stereo WAV header for `frames` frames.
static void wavHeader(uint8_t* h, double rate, uint64_t frames) {
    uint32_t data = (uint32_t)(frames * 8);
    memcpy(h, "RIFF", 4); put32(h + 4, kWavHeaderBytes - 8 + data); memcpy(h + 8, "WAVE", 4);
    memcpy(h + 12, "fmt ", 4); put32(h + 16, 16); put16(h + 20, 3); put16(h + 22, 2);
    put32(h + 24, (uint32_t)rate); put32(h + 28, (uint32_t)rate * 8); put16(h + 32, 8); put16(h + 34, 32);
    memcpy(h + 36, "fact", 4); put32(h + 40, 4); put32(h + 44, (uint32_t)frames);
    memcpy(h + 48, "data", 4); put32(h + 52, data);
}

static void* recordWriter(void* context) {
    Bridge* b = context;
    static float block[kMaxBlock * 2];
    for (;;) {
        bool stopping = atomic_load(&b->stopWriter);
        if (stopping) usleep(20000);  // let a callback that was mid-push finish
        uint32_t n;
        bool full = false;
        while (!full && (n = ringPop(&b->record, block, kMaxBlock)) > 0) {
            if (b->recorded + n >= kMaxWavFrames) { n = (uint32_t)(kMaxWavFrames - b->recorded); full = true; }
            if (fwrite(block, 2 * sizeof(float), n, b->file) != n) { atomic_store(&b->writeFailed, true); full = true; }
            else b->recorded += n;
        }
        if (stopping || full) break;
        usleep(20000);
    }
    atomic_store(&b->recording, false);
    return NULL;
}

// Starts recording into a new file at `path`; refuses an existing name. Returns NULL or what went wrong.
static const char* recordStart(Bridge* b, const char* path) {
    if (atomic_load(&b->recording)) return "Already recording";
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0644);
    if (fd < 0) return errno == EEXIST ? "exists" : strerror(errno);
    b->file = fdopen(fd, "wb");
    snprintf(b->path, sizeof(b->path), "%s", path);
    b->recordRate = atomic_load(&b->outRate);
    b->recorded = 0;
    uint8_t header[kWavHeaderBytes];
    wavHeader(header, b->recordRate, 0);
    fwrite(header, 1, sizeof(header), b->file);
    ringPop(&b->record, NULL, UINT32_MAX);  // nothing from before
    atomic_store(&b->writeFailed, false);
    atomic_store(&b->stopWriter, false);
    atomic_store(&b->recording, true);
    pthread_create(&b->writer, NULL, recordWriter, b);
    return NULL;
}

// Finishes the file; returns its length in seconds (negative if writing failed).
static double recordStop(Bridge* b) {
    if (b->file == NULL) return 0;
    atomic_store(&b->stopWriter, true);
    pthread_join(b->writer, NULL);
    atomic_store(&b->recording, false);
    uint8_t header[kWavHeaderBytes];
    wavHeader(header, b->recordRate, b->recorded);
    bool ok = !atomic_load(&b->writeFailed) && fseek(b->file, 0, SEEK_SET) == 0 && fwrite(header, 1, sizeof(header), b->file) == sizeof(header);
    ok = fclose(b->file) == 0 && ok;
    b->file = NULL;
    return ok ? b->recorded / b->recordRate : -1;
}

// ---------------------------------------------------------------------------------------------- Core Audio
static AudioObjectPropertyAddress address(AudioObjectPropertySelector selector, AudioObjectPropertyScope scope) {
    return (AudioObjectPropertyAddress){selector, scope, kAudioObjectPropertyElementMain};
}

static AudioObjectID deviceForUID(const char* uid) {
    CFStringRef string = CFStringCreateWithCString(NULL, uid, kCFStringEncodingUTF8);
    AudioObjectID device = kAudioObjectUnknown;
    UInt32 size = sizeof(device);
    AudioObjectPropertyAddress a = address(kAudioHardwarePropertyTranslateUIDToDevice, kAudioObjectPropertyScopeGlobal);
    OSStatus status = AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, sizeof(string), &string, &size, &device);
    CFRelease(string);
    return status == 0 ? device : kAudioObjectUnknown;
}

static AudioObjectID defaultOutput(void) {
    AudioObjectID device = kAudioObjectUnknown;
    UInt32 size = sizeof(device);
    AudioObjectPropertyAddress a = address(kAudioHardwarePropertyDefaultOutputDevice, kAudioObjectPropertyScopeGlobal);
    return AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, &size, &device) == 0 ? device : kAudioObjectUnknown;
}

static void copyString(AudioObjectID object, AudioObjectPropertySelector selector, char* out, size_t size) {
    CFStringRef value = NULL;
    UInt32 dataSize = sizeof(value);
    AudioObjectPropertyAddress a = address(selector, kAudioObjectPropertyScopeGlobal);
    out[0] = 0;
    if (AudioObjectGetPropertyData(object, &a, 0, NULL, &dataSize, &value) == 0 && value) {
        CFStringGetCString(value, out, (CFIndex)size, kCFStringEncodingUTF8);
        CFRelease(value);
    }
}

static UInt32 getUInt32(AudioObjectID object, AudioObjectPropertySelector selector, AudioObjectPropertyScope scope) {
    UInt32 value = 0, size = sizeof(value);
    AudioObjectPropertyAddress a = address(selector, scope);
    return AudioObjectGetPropertyData(object, &a, 0, NULL, &size, &value) == 0 ? value : 0;
}

static double nominalRate(AudioObjectID device) {
    Float64 rate = 0;
    UInt32 size = sizeof(rate);
    AudioObjectPropertyAddress a = address(kAudioDevicePropertyNominalSampleRate, kAudioObjectPropertyScopeGlobal);
    return AudioObjectGetPropertyData(device, &a, 0, NULL, &size, &rate) == 0 ? rate : 0;
}

static UInt32 channelCount(AudioObjectID device, AudioObjectPropertyScope scope) {
    AudioObjectPropertyAddress a = address(kAudioDevicePropertyStreamConfiguration, scope);
    UInt32 size = 0, channels = 0;
    if (AudioObjectGetPropertyDataSize(device, &a, 0, NULL, &size) != 0 || size == 0) return 0;
    AudioBufferList* list = malloc(size);
    if (AudioObjectGetPropertyData(device, &a, 0, NULL, &size, list) == 0)
        for (UInt32 i = 0; i < list->mNumberBuffers; i++) channels += list->mBuffers[i].mNumberChannels;
    free(list);
    return channels;
}

static double readSpeed(AudioObjectID device) {
    CFPropertyListRef value = NULL;
    UInt32 size = sizeof(value);
    double speed = 0;
    AudioObjectPropertyAddress a = address(kCurrentSpeed, kAudioObjectPropertyScopeGlobal);
    if (AudioObjectGetPropertyData(device, &a, 0, NULL, &size, &value) == 0 && value) {
        if (CFGetTypeID(value) == CFNumberGetTypeID()) CFNumberGetValue(value, kCFNumberDoubleType, &speed);
        CFRelease(value);
    }
    return speed > 0 ? speed : 1.0;
}

static void writeSpeed(AudioObjectID device, double speed) {
    CFNumberRef value = CFNumberCreate(NULL, kCFNumberDoubleType, &speed);
    AudioObjectPropertyAddress a = address(kTargetSpeed, kAudioObjectPropertyScopeGlobal);
    AudioObjectSetPropertyData(device, &a, 0, NULL, sizeof(value), &value);
    CFRelease(value);
}

// Tells Core Audio our callback doesn't use one side of a device (an interface's inputs, the device's outputs).
static void ignoreStreams(AudioObjectID device, AudioDeviceIOProcID proc, AudioObjectPropertyScope scope) {
    AudioObjectPropertyAddress a = address(kAudioDevicePropertyIOProcStreamUsage, scope);
    UInt32 size = 0;
    if (AudioObjectGetPropertyDataSize(device, &a, 0, NULL, &size) != 0 || size < sizeof(AudioHardwareIOProcStreamUsage)) return;
    AudioHardwareIOProcStreamUsage* usage = calloc(1, size);
    usage->mIOProc = (void*)proc;
    if (AudioObjectGetPropertyData(device, &a, 0, NULL, &size, usage) == 0) {
        for (UInt32 i = 0; i < usage->mNumberStreams; i++) usage->mStreamIsOn[i] = 0;
        AudioObjectSetPropertyData(device, &a, 0, NULL, size, usage);
    }
    free(usage);
}

// Where channel `channel` (counting across all buffers) lives: its first sample, the step between frames, frames.
static float* channelData(const AudioBufferList* list, UInt32 channel, UInt32* stride, UInt32* frames) {
    for (UInt32 i = 0; i < list->mNumberBuffers; i++) {
        const AudioBuffer* buffer = &list->mBuffers[i];
        if (channel < buffer->mNumberChannels) {
            *stride = buffer->mNumberChannels;
            *frames = buffer->mDataByteSize / (UInt32)(sizeof(float) * buffer->mNumberChannels);
            return (float*)buffer->mData + channel;
        }
        channel -= buffer->mNumberChannels;
    }
    return NULL;
}

static void jsonString(char* out, size_t size, const char* text) {
    size_t n = 0;
    for (const unsigned char* p = (const unsigned char*)text; *p && n + 7 < size; p++) {
        if (*p == '"' || *p == '\\') { out[n++] = '\\'; out[n++] = (char)*p; }
        else if (*p < 0x20) n += (size_t)snprintf(out + n, size - n, "\\u%04x", *p);
        else out[n++] = (char)*p;
    }
    out[n] = 0;
}

static pthread_mutex_t gPrintLock = PTHREAD_MUTEX_INITIALIZER;
static void emit(const char* format, ...) __attribute__((format(printf, 1, 2)));
static void emit(const char* format, ...) {
    pthread_mutex_lock(&gPrintLock);
    va_list args;
    va_start(args, format);
    vprintf(format, args);
    va_end(args);
    putchar('\n');
    fflush(stdout);
    pthread_mutex_unlock(&gPrintLock);
}

static void emitError(const char* message) {
    char text[1200];
    jsonString(text, sizeof(text), message);
    emit("{\"type\":\"error\",\"message\":\"%s\"}", text);
}

// ---------------------------------------------------------------------------------------------- running
typedef struct {
    AudioObjectID device;
    AudioDeviceIOProcID proc;
    bool running;
    uint64_t started;
    _Atomic uint64_t lastCall;      // host time of the latest callback
} Side;

static Bridge gBridge;
static Side gIn, gOut;
static pthread_mutex_t gLock = PTHREAD_MUTEX_INITIALIZER;  // devices and settings (never taken in callbacks)
static _Atomic UInt32 gLeft = 0;
static char gOutputUID[512] = "", gOutputName[256] = "";
static bool gOutputMissing = false;
static double gTarget = 1.0;
static UInt32 gInBuffer = 0, gOutLatency = 0;
static const char* gMicrophone = "undetermined";
static _Atomic bool gQuit = false;
static double gTicksPerSecond = 1e9;

static OSStatus inputProc(AudioObjectID device, const AudioTimeStamp* now, const AudioBufferList* input,
                          const AudioTimeStamp* inputTime, AudioBufferList* output, const AudioTimeStamp* outputTime, void* context) {
    (void)device; (void)now; (void)inputTime; (void)output; (void)outputTime; (void)context;
    UInt32 strideL = 0, strideR = 0, framesL = 0, framesR = 0;
    const float* left = channelData(input, 0, &strideL, &framesL);
    const float* right = channelData(input, 1, &strideR, &framesR);
    if (left == NULL) return 0;
    if (right == NULL) { right = left; strideR = strideL; framesR = framesL; }
    UInt32 frames = framesL < framesR ? framesL : framesR;
    float block[kMaxBlock * 2];
    for (UInt32 done = 0; done < frames;) {
        UInt32 n = frames - done < kMaxBlock ? frames - done : kMaxBlock;
        for (UInt32 i = 0; i < n; i++) {
            block[i * 2] = left[(done + i) * strideL];
            block[i * 2 + 1] = right[(done + i) * strideR];
        }
        bridgeInput(&gBridge, block, n);
        done += n;
    }
    atomic_store_explicit(&gBridge.inChunk, frames, memory_order_relaxed);
    atomic_store_explicit(&gIn.lastCall, mach_absolute_time(), memory_order_relaxed);
    return 0;
}

static OSStatus outputProc(AudioObjectID device, const AudioTimeStamp* now, const AudioBufferList* input,
                           const AudioTimeStamp* inputTime, AudioBufferList* output, const AudioTimeStamp* outputTime, void* context) {
    (void)device; (void)now; (void)input; (void)inputTime; (void)outputTime; (void)context;
    UInt32 left = atomic_load_explicit(&gLeft, memory_order_relaxed);
    UInt32 strideL = 0, strideR = 0, framesL = 0, framesR = 0;
    float* l = channelData(output, left, &strideL, &framesL);
    if (l == NULL) l = channelData(output, left = 0, &strideL, &framesL);  // fewer outputs now: use the first
    float* r = l ? channelData(output, left + 1, &strideR, &framesR) : NULL;
    if (l == NULL) return 0;
    float block[kMaxBlock * 2];
    for (UInt32 done = 0; done < framesL;) {
        UInt32 n = framesL - done < kMaxBlock ? framesL - done : kMaxBlock;
        bridgeRender(&gBridge, block, n);
        for (UInt32 i = 0; i < n; i++) {
            if (r) {
                l[(done + i) * strideL] = block[i * 2];
                if (done + i < framesR) r[(done + i) * strideR] = block[i * 2 + 1];
            } else {
                l[(done + i) * strideL] = 0.5f * (block[i * 2] + block[i * 2 + 1]);  // a mono output
            }
        }
        done += n;
    }
    atomic_store_explicit(&gOut.lastCall, mach_absolute_time(), memory_order_relaxed);
    return 0;
}

static bool startSide(Side* side, AudioObjectID device, AudioDeviceIOProc proc, AudioObjectPropertyScope unused) {
    if (AudioDeviceCreateIOProcID(device, proc, NULL, &side->proc) != 0) return false;
    ignoreStreams(device, side->proc, unused);
    if (AudioDeviceStart(device, side->proc) != 0) {
        AudioDeviceDestroyIOProcID(device, side->proc);
        return false;
    }
    side->device = device;
    side->running = true;
    side->started = mach_absolute_time();
    atomic_store(&side->lastCall, side->started);
    return true;
}

static void stopSide(Side* side) {
    if (!side->running) return;
    AudioDeviceStop(side->device, side->proc);
    AudioDeviceDestroyIOProcID(side->device, side->proc);
    side->running = false;
    side->device = kAudioObjectUnknown;
}

// No callback for a second: the device went away or macOS's audio restarted (after Install, for one).
static bool stalled(Side* side) {
    uint64_t now = mach_absolute_time();
    return side->running && (now - side->started) > gTicksPerSecond && (now - atomic_load(&side->lastCall)) > gTicksPerSecond;
}

static const char* microphoneStatus(void) {
    switch ([AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio]) {
        case AVAuthorizationStatusAuthorized: return "granted";
        case AVAuthorizationStatusDenied: return "denied";
        case AVAuthorizationStatusRestricted: return "restricted";
        default: return "undetermined";
    }
}

// Every half second: finds the devices (the DAW Multitool device appears after Install and gets a new ID whenever
// macOS's audio restarts) and keeps both sides running. The output is the chosen one, or the Mac's default while
// the chosen one isn't connected.
static void checkDevices(void) {
    gMicrophone = microphoneStatus();
    AudioObjectID ours = deviceForUID(kDeviceUID);
    if (gIn.running && (ours != gIn.device || stalled(&gIn))) stopSide(&gIn);
    if (!gIn.running && ours) {
        if (strcmp(gMicrophone, "undetermined") == 0)  // macOS counts any audio input as the microphone
            [AVCaptureDevice requestAccessForMediaType:AVMediaTypeAudio completionHandler:^(BOOL granted) { (void)granted; }];
        if (startSide(&gIn, ours, inputProc, kAudioObjectPropertyScopeOutput)) writeSpeed(ours, gTarget);
    }
    AudioObjectID wanted = gOutputUID[0] ? deviceForUID(gOutputUID) : kAudioObjectUnknown;
    gOutputMissing = gOutputUID[0] && wanted == kAudioObjectUnknown;
    if (wanted == kAudioObjectUnknown) wanted = defaultOutput();
    if (wanted == ours) wanted = kAudioObjectUnknown;  // never back into itself
    if (gOut.running && (wanted != gOut.device || stalled(&gOut))) stopSide(&gOut);
    if (!gOut.running && wanted) {
        gBridge.primed = false;  // the output callback isn't running, so this is safe
        atomic_store(&gBridge.outRate, nominalRate(wanted));
        if (startSide(&gOut, wanted, outputProc, kAudioObjectPropertyScopeInput))
            copyString(wanted, kAudioObjectPropertyName, gOutputName, sizeof(gOutputName));
    }
}

static void checkRates(void) {
    if (gIn.running) {
        double rate = nominalRate(gIn.device);
        if (rate > 0) atomic_store(&gBridge.inRate, rate);
        gInBuffer = getUInt32(gIn.device, kAudioDevicePropertyBufferFrameSize, kAudioObjectPropertyScopeGlobal);
    }
    if (gOut.running) {
        double rate = nominalRate(gOut.device);
        if (rate > 0 && rate != atomic_load(&gBridge.outRate)) {
            atomic_store(&gBridge.outRate, rate);
            if (atomic_load(&gBridge.recording)) {
                double seconds = recordStop(&gBridge);
                char path[1100];
                jsonString(path, sizeof(path), gBridge.path);
                emit("{\"type\":\"recorded\",\"path\":\"%s\",\"seconds\":%.3f}", path, seconds);
                emitError("The output's sample rate changed, so the recording stopped there.");
            }
        }
        gOutLatency = getUInt32(gOut.device, kAudioDevicePropertyLatency, kAudioObjectPropertyScopeOutput) +
                      getUInt32(gOut.device, kAudioDevicePropertySafetyOffset, kAudioObjectPropertyScopeOutput) +
                      getUInt32(gOut.device, kAudioDevicePropertyBufferFrameSize, kAudioObjectPropertyScopeGlobal);
    }
}

static void printStatus(void) {
    double inRate = atomic_load(&gBridge.inRate), outRate = atomic_load(&gBridge.outRate), speed = atomic_load(&gBridge.speed);
    double latency = gIn.running && gOut.running && inRate > 0 && outRate > 0
        ? (ringFill(&gBridge.input) / (inRate * speed) + gInBuffer / inRate + gOutLatency / outRate) * 1000 : 0;
    bool recording = atomic_load(&gBridge.recording);
    if (!recording && gBridge.file) {  // the writer stopped by itself: the disk is full or the file got too big
        bool failed = atomic_load(&gBridge.writeFailed);
        double seconds = recordStop(&gBridge);
        char path[1100];
        jsonString(path, sizeof(path), gBridge.path);
        emit("{\"type\":\"recorded\",\"path\":\"%s\",\"seconds\":%.3f}", path, seconds);
        emitError(failed ? "Couldn't keep writing the recording (is the disk full?)." : "The recording reached the 4 GB WAV limit.");
    }
    char name[600];
    jsonString(name, sizeof(name), gOutputName);
    emit("{\"type\":\"status\",\"device\":%s,\"input\":%s,\"output\":%s,\"output_name\":\"%s\",\"output_missing\":%s,"
         "\"left\":%u,\"in_rate\":%.0f,\"out_rate\":%.0f,\"speed\":%.4f,\"target\":%.4f,\"latency_ms\":%.1f,"
         "\"dropouts\":%u,\"level\":%.4f,\"recording\":%s,\"recorded\":%.2f,\"microphone\":\"%s\"}",
         deviceForUID(kDeviceUID) ? "true" : "false", gIn.running ? "true" : "false", gOut.running ? "true" : "false",
         gOut.running ? name : "", gOutputMissing ? "true" : "false", atomic_load(&gLeft), inRate, outRate, speed, gTarget,
         latency, atomic_load(&gBridge.dropouts), atomic_exchange(&gBridge.peak, 0.0f), recording ? "true" : "false",
         gBridge.file ? gBridge.recorded / gBridge.recordRate : 0.0, gMicrophone);
}

static void* supervise(void* unused) {
    (void)unused;
    for (uint64_t tick = 0; !atomic_load(&gQuit); tick++) {
        pthread_mutex_lock(&gLock);
        if (tick % 100 == 0) checkDevices();
        if (gIn.running) atomic_store(&gBridge.speed, readSpeed(gIn.device));  // every 5 ms: glides are quick
        if (tick % 20 == 0) checkRates();
        if (tick % 50 == 0) printStatus();
        pthread_mutex_unlock(&gLock);
        usleep(5000);
    }
    return NULL;
}

static void command(char* line) {
    line[strcspn(line, "\r\n")] = 0;
    pthread_mutex_lock(&gLock);
    if (strncmp(line, "speed ", 6) == 0) {
        double speed = strtod(line + 6, NULL);
        gTarget = speed >= 0.5 && speed <= 2.0 ? speed : (speed > 2.0 ? 2.0 : (speed > 0 ? 0.5 : 1.0));
        if (gIn.running) writeSpeed(gIn.device, gTarget);
    } else if (strncmp(line, "output ", 7) == 0) {
        char* rest = NULL;
        long left = strtol(line + 7, &rest, 10);
        atomic_store(&gLeft, left > 0 ? (UInt32)left : 0);
        snprintf(gOutputUID, sizeof(gOutputUID), "%s", *rest == ' ' ? rest + 1 : rest);
        checkDevices();
    } else if (strncmp(line, "record ", 7) == 0) {
        const char* problem = recordStart(&gBridge, line + 7);
        if (problem) {
            char text[1200];
            snprintf(text, sizeof(text), "Couldn't start recording: %s", problem);
            emitError(text);
        }
    } else if (strcmp(line, "stop-record") == 0) {
        if (gBridge.file) {
            double seconds = recordStop(&gBridge);
            char path[1100];
            jsonString(path, sizeof(path), gBridge.path);
            emit("{\"type\":\"recorded\",\"path\":\"%s\",\"seconds\":%.3f}", path, seconds);
        }
    } else if (strcmp(line, "quit") == 0) {
        atomic_store(&gQuit, true);
    } else if (line[0]) {
        emitError("Unknown command");
    }
    pthread_mutex_unlock(&gLock);
}

static void onSignal(int signal) { (void)signal; atomic_store(&gQuit, true); }

static int run(void) {
    mach_timebase_info_data_t timebase;
    mach_timebase_info(&timebase);
    gTicksPerSecond = 1e9 * timebase.denom / timebase.numer;
    bridgeInit(&gBridge, 48000, 48000);
    signal(SIGPIPE, SIG_IGN);
    struct sigaction action = {0};
    action.sa_handler = onSignal;  // no SA_RESTART: reading stdin stops, so we tidy up below
    sigaction(SIGTERM, &action, NULL);
    sigaction(SIGINT, &action, NULL);
    pthread_t supervisor;
    pthread_create(&supervisor, NULL, supervise, NULL);
    char line[2048];
    while (!atomic_load(&gQuit) && fgets(line, sizeof(line), stdin)) command(line);
    atomic_store(&gQuit, true);
    pthread_join(supervisor, NULL);
    pthread_mutex_lock(&gLock);
    if (gBridge.file) {
        double seconds = recordStop(&gBridge);
        char path[1100];
        jsonString(path, sizeof(path), gBridge.path);
        emit("{\"type\":\"recorded\",\"path\":\"%s\",\"seconds\":%.3f}", path, seconds);
    }
    if (gIn.running) writeSpeed(gIn.device, 1.0);  // the DAW back to normal speed
    stopSide(&gOut);
    stopSide(&gIn);
    pthread_mutex_unlock(&gLock);
    return 0;
}

// ---------------------------------------------------------------------------------------------- --list
static int listDevices(void) {
    AudioObjectPropertyAddress a = address(kAudioHardwarePropertyDevices, kAudioObjectPropertyScopeGlobal);
    UInt32 size = 0;
    AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &a, 0, NULL, &size);
    AudioObjectID* devices = malloc(size + sizeof(AudioObjectID));
    AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, &size, devices);
    char uid[512], name[256], escapedUID[1100], escapedName[600];
    copyString(defaultOutput(), kAudioDevicePropertyDeviceUID, uid, sizeof(uid));
    jsonString(escapedUID, sizeof(escapedUID), uid);
    printf("{\"default_output\":\"%s\",\"devices\":[", escapedUID);
    for (UInt32 i = 0; i < size / sizeof(AudioObjectID); i++) {
        copyString(devices[i], kAudioDevicePropertyDeviceUID, uid, sizeof(uid));
        copyString(devices[i], kAudioObjectPropertyName, name, sizeof(name));
        jsonString(escapedUID, sizeof(escapedUID), uid);
        jsonString(escapedName, sizeof(escapedName), name);
        printf("%s{\"uid\":\"%s\",\"name\":\"%s\",\"inputs\":%u,\"outputs\":%u,\"rate\":%.0f}", i ? "," : "", escapedUID,
               escapedName, channelCount(devices[i], kAudioObjectPropertyScopeInput),
               channelCount(devices[i], kAudioObjectPropertyScopeOutput), nominalRate(devices[i]));
    }
    printf("]}\n");
    free(devices);
    return 0;
}

// ---------------------------------------------------------------------------------------------- tests
static int gFailures = 0;
#define CHECK(cond, ...) do { if (!(cond)) { gFailures++; printf("FAIL line %d: ", __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

typedef struct { double heard; uint32_t dropouts; double worstLatency; } Heard;

// Plays a 440 Hz sine through a bridge with pretend audio callbacks: the DAW side delivers `inChunk` frames at
// inRate·speed·drift a second, the output asks for `outChunk` frames at outRate. With `glide`, the speed starts
// at 1 and steps to `speed` the way the device's clock does (1 semitone a 1024-frame period). Returns the
// frequency heard over the last half, which should be 440·speed·drift.
static Heard simulate(double inRate, double outRate, double speed, double drift, bool glide, double seconds,
                      uint32_t inChunk, uint32_t outChunk) {
    Bridge* b = malloc(sizeof(Bridge));
    bridgeInit(b, inRate, outRate);
    double actual = glide ? 1.0 : speed, nextIn = 0, nextOut = 0.0031, nextPoll = 0, sourceTime = 0, nextPeriod = 0;
    atomic_store(&b->speed, actual);
    uint64_t total = (uint64_t)(seconds * outRate) + outChunk, heardFrames = 0;
    float* heard = malloc(total * 2 * sizeof(float));
    float block[kMaxBlock * 2];
    Heard result = {0};
    while (nextOut < seconds) {
        if (glide && sourceTime >= nextPeriod) {  // the device's clock steps once a period
            double semis = 12 * log2(speed / actual);
            actual *= exp2(fmax(-1.0, fmin(0.5, semis)) / 12);
            nextPeriod += 1024 / inRate;
        }
        if (nextPoll <= fmin(nextIn, nextOut)) {  // the speed as the bridge reads it, every 5 ms
            atomic_store(&b->speed, actual);
            nextPoll += 0.005;
        } else if (nextIn <= nextOut) {
            for (uint32_t i = 0; i < inChunk; i++) {
                float v = 0.5f * (float)sin(2 * M_PI * 440 * (sourceTime + i / inRate));
                block[i * 2] = block[i * 2 + 1] = v;
            }
            bridgeInput(b, block, inChunk);
            atomic_store(&b->inChunk, inChunk);
            sourceTime += inChunk / inRate;
            nextIn += inChunk / (inRate * actual * drift);
        } else {
            bridgeRender(b, block, outChunk);
            memcpy(heard + heardFrames * 2, block, outChunk * 2 * sizeof(float));
            heardFrames += outChunk;
            nextOut += outChunk / outRate;
            if (nextOut > seconds / 2)
                result.worstLatency = fmax(result.worstLatency, ringFill(&b->input) / (inRate * actual));
        }
    }
    double first = -1, last = -1;
    int crossings = 0;
    for (uint64_t i = heardFrames / 2; i + 1 < heardFrames; i++) {
        float x0 = heard[i * 2], x1 = heard[(i + 1) * 2];
        if (x0 < 0 && x1 >= 0) {
            double at = i + x0 / (x0 - x1);
            if (first < 0) first = at;
            last = at;
            crossings++;
        }
    }
    result.heard = crossings > 1 ? (crossings - 1) / (last - first) * outRate : 0;
    result.dropouts = atomic_load(&b->dropouts);
    free(heard);
    free(b->input.frames);
    free(b->record.frames);
    free(b);
    return result;
}

static void testRing(void) {
    Ring r;
    ringInit(&r, 8);
    float in[12] = {1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6}, out[16] = {0};
    CHECK(ringPush(&r, in, 6) == 6 && ringPop(&r, out, 4) == 4 && out[6] == 4, "push and pop");
    CHECK(ringPush(&r, in, 6) == 6 && ringFill(&r) == 8, "wraps round");
    CHECK(ringPush(&r, in, 1) == 0, "never overwrites what wasn't read");
    CHECK(ringPop(&r, out, 8) == 8 && out[0] == 5 && out[4] == 1 && out[14] == 6, "reads in order across the wrap");
    free(r.frames);
}

static void testResampler(void) {
    struct { double inRate, outRate, speed, drift; bool glide; } cases[] = {
        {48000, 48000, 1.0, 1.0, false},       // straight through
        {48000, 48000, 0.5, 1.0, false},       // half speed: an octave down
        {48000, 48000, 2.0, 1.0, false},       // double speed: an octave up
        {44100, 48000, 0.75, 1.0, false},      // the DAW at 44.1k, the interface at 48k
        {96000, 44100, 1.25, 1.0, false},
        {48000, 48000, 1.0, 1.001, false},     // the two clocks disagree by 0.1%
        {48000, 48000, 0.8909, 0.9995, false},
        {48000, 48000, 0.5, 1.0, true},        // gliding down an octave while playing
        {48000, 48000, 2.0, 1.0, true},
    };
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        Heard h = simulate(cases[i].inRate, cases[i].outRate, cases[i].speed, cases[i].drift, cases[i].glide, 6, 512, 256);
        double expected = 440 * cases[i].speed * cases[i].drift;
        CHECK(fabs(h.heard / expected - 1) < 0.001, "case %zu: heard %.2f Hz, expected %.2f", i, h.heard, expected);
        CHECK(h.dropouts == 0, "case %zu: %u dropouts", i, h.dropouts);
        CHECK(h.worstLatency < 0.08, "case %zu: latency %.1f ms", i, h.worstLatency * 1000);
    }
    struct { double inRate, outRate, speed, drift; uint32_t inChunk, outChunk; } buffers[] = {
        {48000, 48000, 1.0, 1.0, 2048, 512},       // a DAW with a big buffer
        {48000, 48000, 0.5, 1.0, 64, 512},         // a tiny DAW buffer, a big output buffer
        {44100, 48000, 2.0, 1.0, 1024, 128},
        {48000, 44100, 1.5, 1.0004, 256, 1024},
        {88200, 48000, 0.6, 0.9996, 4096, 256},
    };
    for (size_t i = 0; i < sizeof(buffers) / sizeof(buffers[0]); i++) {
        Heard h = simulate(buffers[i].inRate, buffers[i].outRate, buffers[i].speed, buffers[i].drift, false, 5,
                           buffers[i].inChunk, buffers[i].outChunk);
        double expected = 440 * buffers[i].speed * buffers[i].drift;
        CHECK(h.dropouts == 0 && fabs(h.heard / expected - 1) < 0.001, "buffers %zu: %u dropouts, %.2f Hz, expected %.2f",
              i, h.dropouts, h.heard, expected);
    }
    Heard glide = simulate(44100, 48000, 0.5, 1.0005, true, 6, 512, 512);  // a glide while the clocks disagree
    CHECK(glide.dropouts == 0 && fabs(glide.heard / (220 * 1.0005) - 1) < 0.001, "glide with drift: %u dropouts, %.2f Hz",
          glide.dropouts, glide.heard);
}

static void testRecording(const char* folder) {
    char path[1024];
    snprintf(path, sizeof(path), "%s/bridge test.wav", folder);
    Bridge* b = malloc(sizeof(Bridge));
    bridgeInit(b, 48000, 48000);
    CHECK(recordStart(b, path) == NULL, "starts recording");
    float block[512 * 2];
    for (int n = 0; n < 94; n++) {  // about a second
        for (int i = 0; i < 512; i++) block[i * 2] = block[i * 2 + 1] = 0.25f * (float)sin((n * 512 + i) * 0.05);
        bridgeInput(b, block, 512);
        atomic_store(&b->inChunk, 512);
        bridgeRender(b, block, 512);
        usleep(1000);
    }
    double seconds = recordStop(b);
    CHECK(fabs(seconds - 94 * 512 / 48000.0) < 1e-6, "recorded %.3f s", seconds);
    FILE* f = fopen(path, "rb");
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fclose(f);
    CHECK(size == kWavHeaderBytes + 94 * 512 * 8, "file size %ld", size);
    const char* again = recordStart(b, path);
    CHECK(again != NULL && strcmp(again, "exists") == 0, "never replaces a file");
    free(b->input.frames);
    free(b->record.frames);
    free(b);
}

int main(int argc, char** argv) {
    @autoreleasepool {
        if (argc > 1 && strcmp(argv[1], "--list") == 0) return listDevices();
        if (argc > 1 && strcmp(argv[1], "--self-check") == 0) {
            Heard h = simulate(44100, 48000, 0.75, 1.0, false, 1, 512, 256);
            bool ok = h.dropouts == 0 && fabs(h.heard / 330 - 1) < 0.002;
            if (ok) printf("ok\n");
            else printf("FAIL resampler: %.2f Hz, %u dropouts\n", h.heard, h.dropouts);
            return ok ? 0 : 1;
        }
        if (argc > 2 && strcmp(argv[1], "--test") == 0) {
            testRing();
            testResampler();
            testRecording(argv[2]);
            if (gFailures) printf("%d failed\n", gFailures);
            else printf("ok\n");
            return gFailures ? 1 : 0;
        }
        if (argc > 1) {
            fprintf(stderr, "usage: audio-bridge [--list | --self-check | --test <folder>]\n");
            return 2;
        }
        return run();
    }
}
