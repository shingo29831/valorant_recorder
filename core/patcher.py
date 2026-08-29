import sys

def patch_soundcard_lib():
    """
    soundcardライブラリがWAVE_FORMAT_PCMやWAVE_FORMAT_IEEE_FLOATなどの
    非拡張フォーマットのデバイスを読み込もうとした際にクラッシュするバグを修正するモンキーパッチ。
    """
    if sys.platform != 'win32':
        return

    try:
        import ctypes
        # PyQt6(Qt)はSTA(Single-Threaded Apartment)を要求するため、
        # soundcardがMTAで初期化してしまう前にSTAでCOMを初期化しておく
        try:
            ctypes.windll.ole32.CoInitialize(None)
        except Exception:
            pass

        try:
            import soundcard
        except RuntimeError:
            # COM初期化エラー(S_FALSE等)が発生しても無視してパッチを適用する
            pass
            
        import soundcard.mediafoundation as mf
        import collections

        # _AudioClient.__init__ のモンキーパッチ (assertの無効化)
        def patched_audio_client_init(self, ptr, samplerate, channels, blocksize, isloopback, exclusive_mode=False):
            self._ptr = ptr
            if isinstance(channels, int):
                self.channelmap = list(range(channels))
            elif isinstance(channels, collections.abc.Iterable):
                self.channelmap = channels
            else:
                raise TypeError('channels must be iterable or integer')
                
            if list(range(len(set(self.channelmap)))) != sorted(list(set(self.channelmap))):
                raise TypeError('Due to limitations of WASAPI, channel maps on Windows '
                                'must be a combination of `range(0, x)`.')
                                
            if blocksize is None:
                blocksize = self.deviceperiod[0] * samplerate
                
            ppMixFormat = mf._ffi.new('WAVEFORMATEXTENSIBLE**')
            hr = self._ptr[0][0].lpVtbl.GetMixFormat(self._ptr[0], ppMixFormat)
            mf._com.check_error(hr)
            
            # --- パッチ: assertを削除 ---
            # assert ppMixFormat[0][0].Format.wFormatTag == 0xFFFE
            # assert ppMixFormat[0][0].Format.cbSize == 22
            # assert ppMixFormat[0][0].SubFormat.Data1 == 0x100000
            # assert ppMixFormat[0][0].SubFormat.Data2 == 0x0080
            # assert ppMixFormat[0][0].SubFormat.Data3 == 0xaa00
            # assert [int(x) for x in ppMixFormat[0][0].SubFormat.Data4[0:4]] == [0, 56, 155, 113]
            # -----------------------------
            
            channels_count = len(set(self.channelmap))
            channelmask = 0
            for ch in self.channelmap:
                channelmask |= 1 << ch
                
            ppMixFormat[0][0].Format.nChannels = channels_count
            ppMixFormat[0][0].Format.nSamplesPerSec = int(samplerate)
            ppMixFormat[0][0].Format.nAvgBytesPerSec = int(samplerate) * channels_count * 4
            ppMixFormat[0][0].Format.nBlockAlign = channels_count * 4
            ppMixFormat[0][0].Format.wBitsPerSample = 32
            ppMixFormat[0][0].Samples = dict(wValidBitsPerSample=32)
            
            if exclusive_mode:
                sharemode = mf._ole32.AUDCLNT_SHAREMODE_EXCLUSIVE
            else:
                sharemode = mf._ole32.AUDCLNT_SHAREMODE_SHARED
                
            streamflags = 0x00100000 | 0x80000000 | 0x08000000 | 0x00080000
            if isloopback:
                streamflags |= 0x00020000 #loopback
                
            bufferduration = int(blocksize / samplerate * 10000000)
            hr = self._ptr[0][0].lpVtbl.Initialize(self._ptr[0], sharemode, streamflags, bufferduration, 0, ppMixFormat[0], mf._ffi.NULL)
            mf._com.check_error(hr)
            mf._ole32.CoTaskMemFree(ppMixFormat[0])
            
            self.samplerate = samplerate
            self._idle_start_time = None

        mf._AudioClient.__init__ = patched_audio_client_init

        # _COMLibrary.__init__ のモンキーパッチ (COM初期化の競合対策)
        def patched_com_library_init(self):
            import platform
            COINIT_MULTITHREADED = 0x0
            if platform.win32_ver()[0] == '8':
                hr = mf._ole32.CoInitialize(mf._ffi.NULL)
            else:
                hr = mf._ole32.CoInitializeEx(mf._ffi.NULL, COINIT_MULTITHREADED)
            try:
                if hr == 1: # S_FALSE
                    pass
                else:
                    self.check_error(hr)
                self.com_loaded = True
            except RuntimeError as e:
                RPC_E_CHANGED_MODE = 0x80010106
                if hr + 2 ** 32 == RPC_E_CHANGED_MODE or hr == RPC_E_CHANGED_MODE:
                    self.com_loaded = False
                else:
                    raise e

        mf._COMLibrary.__init__ = patched_com_library_init
        
        # 既に初期化されている _com インスタンスを再初期化する
        if hasattr(mf, '_com') and isinstance(mf._com, mf._COMLibrary):
            mf._com.__init__()

        print("[Patcher] Successfully applied monkey patch to soundcard library.")
            
    except Exception as e:
        print(f"[Patcher] Failed to apply monkey patch to soundcard library: {e}")
