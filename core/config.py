import os
from dotenv import load_dotenv

class Config:
    def __init__(self):
        load_dotenv()
        self.REGION = os.environ.get("VALORANT_REGION", "ap")
        self.RIOT_ID = os.environ.get("VALORANT_RIOT_ID", "shingo")
        self.TAG_LINE = os.environ.get("VALORANT_TAG_LINE", "7445")
        self.API_KEY = os.environ.get("HENRIK_API_KEY", "HDEV-2cc41137-127c-41e1-a60e-7dcc90ab0739")
        self.SAVE_DIR = os.environ.get("RECORD_SAVE_DIR", "./records")
        
        self.RECORD_VIDEO_FORMAT = os.environ.get("RECORD_VIDEO_FORMAT", "gdigrab")
        self.RECORD_INPUT_SOURCE = os.environ.get("RECORD_INPUT_SOURCE", "desktop")
        self.RECORD_AUDIO_SYSTEM = os.environ.get("RECORD_AUDIO_SYSTEM", "default")
        self.RECORD_AUDIO_MIC = os.environ.get("RECORD_AUDIO_MIC", "")
        self.RECORD_ENCODER = os.environ.get("RECORD_ENCODER", "h264_nvenc")
        self.RECORD_FPS = os.environ.get("RECORD_FPS", "60")
        self.RECORD_RESOLUTION = os.environ.get("RECORD_RESOLUTION", "1920x1080")