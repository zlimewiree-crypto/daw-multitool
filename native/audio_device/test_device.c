// Tests the DAW Multitool audio device without installing it: loads the built driver into this process with a
// stand-in for Core Audio's host, then checks its properties, the varispeed clock, the loopback and a sample-rate
// change. Usage: test_device <path to DAWMultitoolAudio.driver/Contents/MacOS/DAWMultitoolAudio>
// Prints "ok" and exits 0 when every check passes.
#include <CoreAudio/AudioServerPlugIn.h>
#include <dlfcn.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static int failures = 0;
#define CHECK(cond, ...) do { if (!(cond)) { failures++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static int configRequests = 0, propertyChanges = 0;
static CFMutableDictionaryRef storage;

static OSStatus HostPropertiesChanged(AudioServerPlugInHostRef h, AudioObjectID o, UInt32 n, const AudioObjectPropertyAddress* a) {
    (void)h; (void)o; (void)a; propertyChanges += (int)n; return 0;
}
static OSStatus HostCopyFromStorage(AudioServerPlugInHostRef h, CFStringRef key, CFPropertyListRef* out) {
    (void)h;
    CFPropertyListRef value = CFDictionaryGetValue(storage, key);
    *out = value ? CFRetain(value) : NULL;
    return 0;
}
static OSStatus HostWriteToStorage(AudioServerPlugInHostRef h, CFStringRef key, CFPropertyListRef value) {
    (void)h; CFDictionarySetValue(storage, key, value); return 0;
}
static OSStatus HostDeleteFromStorage(AudioServerPlugInHostRef h, CFStringRef key) {
    (void)h; CFDictionaryRemoveValue(storage, key); return 0;
}
static OSStatus HostRequestConfigChange(AudioServerPlugInHostRef h, AudioObjectID device, UInt64 action, void* info) {
    (void)h; (void)device; (void)action; (void)info; configRequests++; return 0;
}
static AudioServerPlugInHostInterface host = {
    HostPropertiesChanged, HostCopyFromStorage, HostWriteToStorage, HostDeleteFromStorage, HostRequestConfigChange,
};

static AudioServerPlugInDriverRef driver;
#define D (*driver)

static AudioObjectPropertyAddress address(AudioObjectPropertySelector selector, AudioObjectPropertyScope scope) {
    return (AudioObjectPropertyAddress){selector, scope, kAudioObjectPropertyElementMain};
}

static UInt32 getUInt32(AudioObjectID object, AudioObjectPropertySelector selector) {
    AudioObjectPropertyAddress a = address(selector, kAudioObjectPropertyScopeGlobal);
    UInt32 value = 0xFFFFFFFF, size = 0;
    OSStatus status = D->GetPropertyData(driver, object, 0, &a, 0, NULL, sizeof(value), &size, &value);
    CHECK(status == 0 && size == sizeof(value), "UInt32 property '%.4s' on %u: status %d", (char*)&selector, object, (int)status);
    return value;
}

static int stringIs(AudioObjectID object, AudioObjectPropertySelector selector, const char* expected) {
    AudioObjectPropertyAddress a = address(selector, kAudioObjectPropertyScopeGlobal);
    CFStringRef value = NULL;
    UInt32 size = 0;
    if (D->GetPropertyData(driver, object, 0, &a, 0, NULL, sizeof(value), &size, &value) != 0 || value == NULL) return 0;
    char text[256] = {0};
    CFStringGetCString(value, text, sizeof(text), kCFStringEncodingUTF8);
    CFRelease(value);
    return strcmp(text, expected) == 0;
}

static double getNumber(AudioObjectPropertySelector selector) {
    AudioObjectPropertyAddress a = address(selector, kAudioObjectPropertyScopeGlobal);
    CFPropertyListRef value = NULL;
    UInt32 size = 0;
    double number = -1;
    OSStatus status = D->GetPropertyData(driver, 2, 0, &a, 0, NULL, sizeof(value), &size, &value);
    CHECK(status == 0 && value != NULL, "custom property '%.4s'", (char*)&selector);
    if (value) { CFNumberGetValue(value, kCFNumberDoubleType, &number); CFRelease(value); }
    return number;
}

static OSStatus setTargetSpeed(double speed) {
    AudioObjectPropertyAddress a = address('dmts', kAudioObjectPropertyScopeGlobal);
    CFNumberRef value = CFNumberCreate(NULL, kCFNumberDoubleType, &speed);
    OSStatus status = D->SetPropertyData(driver, 2, 0, &a, 0, NULL, sizeof(value), &value);
    CFRelease(value);
    return status;
}

static Float64 getRate(void) {
    AudioObjectPropertyAddress a = address(kAudioDevicePropertyNominalSampleRate, kAudioObjectPropertyScopeGlobal);
    Float64 rate = 0;
    UInt32 size = 0;
    D->GetPropertyData(driver, 2, 0, &a, 0, NULL, sizeof(rate), &size, &rate);
    return rate;
}

// How many frames the device clock covers in `seconds` of real time.
static Float64 framesIn(double seconds) {
    Float64 sampleTime = 0, after = 0;
    UInt64 hostTime = 0, seed = 0;
    D->GetZeroTimeStamp(driver, 2, 0, &sampleTime, &hostTime, &seed);
    usleep((useconds_t)(seconds * 1e6));
    D->GetZeroTimeStamp(driver, 2, 0, &after, &hostTime, &seed);
    return after - sampleTime;
}

static void testProperties(void) {
    CHECK(getUInt32(kAudioObjectPlugInObject, kAudioObjectPropertyClass) == kAudioPlugInClassID, "plug-in class");
    CHECK(getUInt32(kAudioObjectPlugInObject, kAudioPlugInPropertyDeviceList) == 2, "device list");
    CHECK(getUInt32(2, kAudioObjectPropertyClass) == kAudioDeviceClassID, "device class");
    CHECK(stringIs(2, kAudioObjectPropertyName, "DAW Multitool"), "device name");
    CHECK(stringIs(2, kAudioDevicePropertyDeviceUID, "com.dawmultitool.audio.device"), "device UID");
    CHECK(getUInt32(2, kAudioDevicePropertyTransportType) == kAudioDeviceTransportTypeVirtual, "transport");
    CHECK(getUInt32(2, kAudioDevicePropertyDeviceCanBeDefaultDevice) == 0, "never the Mac's default output");
    CHECK(getUInt32(2, kAudioDevicePropertyZeroTimeStampPeriod) == 1024, "period");
    CHECK(getUInt32(2, kAudioDevicePropertyClockAlgorithm) == kAudioDeviceClockAlgorithmRaw, "raw clock");

    AudioObjectPropertyAddress a = address(kAudioPlugInPropertyTranslateUIDToDevice, kAudioObjectPropertyScopeGlobal);
    CFStringRef uid = CFSTR("com.dawmultitool.audio.device");
    AudioObjectID found = 0;
    UInt32 size = 0;
    D->GetPropertyData(driver, kAudioObjectPlugInObject, 0, &a, sizeof(uid), &uid, sizeof(found), &size, &found);
    CHECK(found == 2, "UID to device: %u", found);

    AudioObjectID streams[4] = {0};
    a = address(kAudioDevicePropertyStreams, kAudioObjectPropertyScopeInput);
    D->GetPropertyData(driver, 2, 0, &a, 0, NULL, sizeof(streams), &size, streams);
    CHECK(size == sizeof(AudioObjectID) && streams[0] == 3, "input stream");
    a = address(kAudioDevicePropertyStreams, kAudioObjectPropertyScopeOutput);
    D->GetPropertyData(driver, 2, 0, &a, 0, NULL, sizeof(streams), &size, streams);
    CHECK(size == sizeof(AudioObjectID) && streams[0] == 4, "output stream");
    CHECK(getUInt32(3, kAudioStreamPropertyDirection) == 1 && getUInt32(4, kAudioStreamPropertyDirection) == 0, "directions");

    AudioStreamBasicDescription format = {0};
    a = address(kAudioStreamPropertyVirtualFormat, kAudioObjectPropertyScopeGlobal);
    D->GetPropertyData(driver, 4, 0, &a, 0, NULL, sizeof(format), &size, &format);
    CHECK(format.mChannelsPerFrame == 2 && format.mBitsPerChannel == 32 && (format.mFormatFlags & kAudioFormatFlagIsFloat),
          "stereo 32-bit float");

    a = address(kAudioObjectPropertyName, kAudioObjectPropertyScopeGlobal);
    CHECK(!D->HasProperty(driver, 99, 0, &a), "unknown object");
    a = address('none', kAudioObjectPropertyScopeGlobal);
    CHECK(!D->HasProperty(driver, 2, 0, &a), "unknown property");
}

static void testSpeedProperty(void) {
    CHECK(fabs(getNumber('dmts') - 1.0) < 1e-9, "starts at 100%%");
    CHECK(setTargetSpeed(0.75) == 0 && fabs(getNumber('dmts') - 0.75) < 1e-9, "set 75%%");
    CHECK(setTargetSpeed(9.0) == 0 && fabs(getNumber('dmts') - 2.0) < 1e-9, "clamped to 200%%");
    CHECK(setTargetSpeed(0.1) == 0 && fabs(getNumber('dmts') - 0.5) < 1e-9, "clamped to 50%%");
    AudioObjectPropertyAddress a = address('dmts', kAudioObjectPropertyScopeGlobal);
    Boolean settable = false;
    D->IsPropertySettable(driver, 2, 0, &a, &settable);
    CHECK(settable, "target speed settable");
    a = address('dmcs', kAudioObjectPropertyScopeGlobal);
    D->IsPropertySettable(driver, 2, 0, &a, &settable);
    CHECK(!settable, "current speed read only");
    setTargetSpeed(1.0);
}

static void testClock(void) {
    Float64 rate = getRate();
    CHECK(D->StartIO(driver, 2, 1) == 0, "start");
    CHECK(getUInt32(2, kAudioDevicePropertyDeviceIsRunning) == 1, "running");
    Float64 frames = framesIn(0.5);
    CHECK(fabs(frames - rate * 0.5) <= 2048, "100%%: %.0f frames in 0.5 s at %.0f Hz", frames, rate);

    setTargetSpeed(0.5);
    usleep(600000);  // the glide down an octave takes 12 periods (1 semitone each), about 0.4 s
    framesIn(0.0);
    CHECK(fabs(getNumber('dmcs') - 0.5) < 1e-6, "glided to 50%%: %.4f", getNumber('dmcs'));
    frames = framesIn(0.5);
    CHECK(fabs(frames - rate * 0.25) <= 2048, "50%%: %.0f frames in 0.5 s", frames);

    setTargetSpeed(2.0);  // rises 0.5 semitone a period (about 43 ms at 50%): after 0.1 s it has only begun
    usleep(100000);
    framesIn(0.0);
    double speed = getNumber('dmcs');
    CHECK(speed > 0.5 && speed < 0.6, "rising gradually: %.4f", speed);
    usleep(1200000);
    framesIn(0.0);
    CHECK(fabs(getNumber('dmcs') - 2.0) < 1e-6, "glided to 200%%: %.4f", getNumber('dmcs'));
    frames = framesIn(0.5);
    CHECK(fabs(frames - rate * 1.0) <= 2048, "200%%: %.0f frames in 0.5 s", frames);

    Float64 sampleTime = 0, previous = -1;
    UInt64 hostTime = 0, previousHost = 0, seed = 0;
    for (int i = 0; i < 20; i++) {
        D->GetZeroTimeStamp(driver, 2, 0, &sampleTime, &hostTime, &seed);
        CHECK(sampleTime >= previous && hostTime >= previousHost, "time never runs backwards");
        CHECK(fmod(sampleTime, 1024) == 0, "sample times on period boundaries");
        previous = sampleTime; previousHost = hostTime;
        usleep(3000);
    }
    setTargetSpeed(1.0);
    CHECK(D->StopIO(driver, 2, 1) == 0, "stop");
    CHECK(getUInt32(2, kAudioDevicePropertyDeviceIsRunning) == 0, "stopped");
}

static float loudness(const float* audio, int count) {
    float total = 0;
    for (int i = 0; i < count; i++) total += fabsf(audio[i]);
    return total;
}

static void testLoopback(void) {
    D->StartIO(driver, 2, 1);
    enum { frames = 512 };
    float played[frames * 2], heard[frames * 2];
    for (int i = 0; i < frames * 2; i++) played[i] = (float)sin(i * 0.01);
    AudioServerPlugInIOCycleInfo cycle = {0};
    cycle.mOutputTime.mSampleTime = 65536 - 100;  // wraps round the end of the ring
    cycle.mInputTime.mSampleTime = 65536 - 100;
    Boolean will = false, inPlace = false;
    D->WillDoIOOperation(driver, 2, 1, kAudioServerPlugInIOOperationWriteMix, &will, &inPlace);
    CHECK(will, "writes the mix");
    D->WillDoIOOperation(driver, 2, 1, kAudioServerPlugInIOOperationProcessOutput, &will, &inPlace);
    CHECK(!will, "no output processing");
    D->DoIOOperation(driver, 2, 3, 1, kAudioServerPlugInIOOperationReadInput, frames, &cycle, heard, NULL);
    CHECK(loudness(heard, frames * 2) == 0, "silence before the DAW plays");
    D->DoIOOperation(driver, 2, 4, 1, kAudioServerPlugInIOOperationWriteMix, frames, &cycle, played, NULL);
    D->DoIOOperation(driver, 2, 3, 1, kAudioServerPlugInIOOperationReadInput, frames, &cycle, heard, NULL);
    CHECK(memcmp(played, heard, sizeof(played)) == 0, "the input gives back what was played");
    D->DoIOOperation(driver, 2, 3, 1, kAudioServerPlugInIOOperationReadInput, frames, &cycle, heard, NULL);
    CHECK(memcmp(played, heard, sizeof(played)) == 0, "a second reader hears it too");

    AudioServerPlugInIOCycleInfo later = cycle;
    later.mInputTime.mSampleTime += frames / 2;  // half written, half not yet
    D->DoIOOperation(driver, 2, 3, 1, kAudioServerPlugInIOOperationReadInput, frames, &later, heard, NULL);
    CHECK(memcmp(played + frames, heard, frames * sizeof(float)) == 0 && loudness(heard + frames, frames) == 0,
          "only what's been written");
    later.mInputTime.mSampleTime = cycle.mInputTime.mSampleTime + 65536;  // a lap later, the DAW stopped
    D->DoIOOperation(driver, 2, 3, 1, kAudioServerPlugInIOOperationReadInput, frames, &later, heard, NULL);
    CHECK(loudness(heard, frames * 2) == 0, "no repeating loop when the DAW stops");

    later.mOutputTime.mSampleTime = cycle.mOutputTime.mSampleTime + 65536 + frames;  // the DAW comes back after a gap
    D->DoIOOperation(driver, 2, 4, 1, kAudioServerPlugInIOOperationWriteMix, frames, &later, played, NULL);
    D->DoIOOperation(driver, 2, 3, 1, kAudioServerPlugInIOOperationReadInput, frames, &later, heard, NULL);
    CHECK(loudness(heard, frames * 2) == 0, "the gap reads as silence, not old audio");
    D->StopIO(driver, 2, 1);
}

static void testSampleRate(void) {
    AudioObjectPropertyAddress a = address(kAudioDevicePropertyNominalSampleRate, kAudioObjectPropertyScopeGlobal);
    Float64 rate = 44100;
    int before = configRequests;
    CHECK(D->SetPropertyData(driver, 2, 0, &a, 0, NULL, sizeof(rate), &rate) == 0, "set 44.1k");
    CHECK(configRequests == before + 1, "asks the host to change");
    CHECK(getRate() == 48000, "unchanged until the host agrees");
    D->PerformDeviceConfigurationChange(driver, 2, 0, NULL);
    CHECK(getRate() == 44100, "now 44.1k");
    CHECK(CFDictionaryGetValue(storage, CFSTR("sample rate")) != NULL, "remembered for next time");
    rate = 12345;
    CHECK(D->SetPropertyData(driver, 2, 0, &a, 0, NULL, sizeof(rate), &rate) != 0, "refuses an odd rate");
    rate = 48000;
    D->SetPropertyData(driver, 2, 0, &a, 0, NULL, sizeof(rate), &rate);
    D->PerformDeviceConfigurationChange(driver, 2, 0, NULL);
    CHECK(getRate() == 48000, "back to 48k");
}

int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: test_device <driver binary>\n"); return 2; }
    void* library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!library) { printf("FAIL can't load: %s\n", dlerror()); return 1; }
    void* (*create)(CFAllocatorRef, CFUUIDRef) = (void* (*)(CFAllocatorRef, CFUUIDRef))dlsym(library, "DAWMultitoolAudio_Create");
    if (!create) { printf("FAIL no factory\n"); return 1; }
    CHECK(create(NULL, kAudioServerPlugInDriverInterfaceUUID) == NULL, "refuses other plug-in types");
    void* made = create(NULL, kAudioServerPlugInTypeUUID);
    if (!made) { printf("FAIL factory returned nothing\n"); return 1; }
    AudioServerPlugInDriverRef base = made;
    void* asked = NULL;
    CHECK((*base)->QueryInterface(base, CFUUIDGetUUIDBytes(kAudioServerPlugInDriverInterfaceUUID), &asked) == 0 && asked == base,
          "driver interface");
    driver = base;
    storage = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CHECK(D->Initialize(driver, &host) == 0, "initialize");
    CHECK(getRate() == 48000, "48k to start");

    testProperties();
    testSpeedProperty();
    testClock();
    testLoopback();
    testSampleRate();
    D->Release(driver);
    if (failures) { printf("%d failed\n", failures); return 1; }
    printf("ok\n");
    return 0;
}
