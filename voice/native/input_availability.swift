import CoreAudio

// Hardware state is separate from authorization and from render/capture tickets.
// External microphones remain usable with a closed lid.
func microphoneInputStatus(transport: UInt32?, lidClosed: Bool?, routeChanged: Bool) -> Int32 {
    if routeChanged { return 2 }
    guard let transport else { return 3 }
    if transport != kAudioDeviceTransportTypeBuiltIn { return 0 }
    guard let lidClosed else { return 3 }
    return lidClosed ? 1 : 0
}
