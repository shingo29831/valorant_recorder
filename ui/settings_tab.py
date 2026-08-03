from PyQt6.QtWidgets import QWidget, QFormLayout, QLineEdit, QComboBox, QPushButton, QMessageBox, QVBoxLayout, QLabel
from core.config import Config

class SettingsTab(QWidget):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(40, 40, 40, 40)
        
        title = QLabel("APPLICATION SETTINGS")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #FF4655; margin-bottom: 20px;")
        main_layout.addWidget(title)
        
        form_layout = QFormLayout()
        form_layout.setSpacing(15)
        
        self.fps_input = QComboBox()
        self.fps_input.addItems(["30", "60", "120", "144"])
        self.fps_input.setCurrentText(self.config.RECORD_FPS)
        
        self.encoder_input = QComboBox()
        self.encoder_input.addItems(["h264_nvenc", "libx264", "hevc_nvenc"])
        self.encoder_input.setCurrentText(self.config.RECORD_ENCODER)
        
        self.res_input = QComboBox()
        self.res_input.addItems(["1920x1080", "2560x1440", "1280x720"])
        self.res_input.setCurrentText(self.config.RECORD_RESOLUTION)
        
        self.riot_id_input = QLineEdit(self.config.RIOT_ID)
        self.tag_line_input = QLineEdit(self.config.TAG_LINE)
        self.api_key_input = QLineEdit(self.config.API_KEY)
        
        form_layout.addRow("Riot ID:", self.riot_id_input)
        form_layout.addRow("Tag Line:", self.tag_line_input)
        form_layout.addRow("Henrik API Key:", self.api_key_input)
        form_layout.addRow("Recording FPS:", self.fps_input)
        form_layout.addRow("Encoder:", self.encoder_input)
        form_layout.addRow("Resolution:", self.res_input)
        
        main_layout.addLayout(form_layout)
        
        save_btn = QPushButton("SAVE SETTINGS")
        save_btn.setFixedWidth(200)
        save_btn.setStyleSheet("margin-top: 30px;")
        save_btn.clicked.connect(self.save_settings)
        main_layout.addWidget(save_btn)
        
        main_layout.addStretch()
        self.setLayout(main_layout)

    def save_settings(self):
        self.config.RIOT_ID = self.riot_id_input.text()
        self.config.TAG_LINE = self.tag_line_input.text()
        self.config.API_KEY = self.api_key_input.text()
        self.config.RECORD_FPS = self.fps_input.currentText()
        self.config.RECORD_ENCODER = self.encoder_input.currentText()
        self.config.RECORD_RESOLUTION = self.res_input.currentText()
        
        self.config.save()
        QMessageBox.information(self, "Success", "Settings saved successfully.\nPlease restart the application to apply changes.")