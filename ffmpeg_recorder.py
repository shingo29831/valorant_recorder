        samplerate = 48000
        frames_per_buffer = 1024
        mic_gain = float(getattr(self.config, 'RECORD_AUDIO_MIC_GAIN', '1.0'))
        system_gain = float(getattr(self.config, 'RECORD_AUDIO_SYSTEM_GAIN', '1.0'))
        
        try:
