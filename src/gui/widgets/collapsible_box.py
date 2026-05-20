from PyQt5.QtWidgets import QWidget, QToolButton, QVBoxLayout, QFrame, QSizePolicy
from PyQt5.QtCore import Qt, QParallelAnimationGroup, QPropertyAnimation, QAbstractAnimation

class CollapsibleBox(QWidget):
    def __init__(self, title="", parent=None):
        super(CollapsibleBox, self).__init__(parent)

        self.toggle_button = QToolButton(text=title, checkable=True, checked=False)
        self.toggle_button.setStyleSheet("QToolButton { border: none; }")
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.RightArrow)
        self.toggle_button.pressed.connect(self.on_pressed)

        self.toggle_animation = QParallelAnimationGroup(self)

        self.content_area = QFrame()
        self.content_area.setMaximumHeight(0)
        self.content_area.setMinimumHeight(0)
        self.content_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.content_area.setFrameShape(QFrame.NoFrame)

        lay = QVBoxLayout(self)
        lay.setSpacing(0)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.toggle_button)
        lay.addWidget(self.content_area)

        self.content_animation = QPropertyAnimation(self.content_area, b"maximumHeight")
        self.content_animation.setDuration(150) # ms
        self.toggle_animation.addAnimation(self.content_animation)

    def on_pressed(self):
        checked = self.toggle_button.isChecked()
        self.toggle_button.setArrowType(Qt.DownArrow if not checked else Qt.RightArrow) # toggle checked state happens after pressed
        self.toggle_animation.setDirection(QAbstractAnimation.Forward if not checked else QAbstractAnimation.Backward)

        content_height = self.content_area.layout().sizeHint().height()
        self.content_animation.setStartValue(0)
        self.content_animation.setEndValue(content_height)

        self.toggle_animation.start()

    def set_content_layout(self, layout):
        old_layout = self.content_area.layout()
        if old_layout:
            QWidget().setLayout(old_layout) # Delete old
        self.content_area.setLayout(layout)

    def set_content_widget(self, widget):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(widget)
        self.set_content_layout(layout)
