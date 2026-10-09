from dataclasses import dataclass
from typing import List, Optional
from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication, QScreen


@dataclass
class DisplayInfo:
    """Represents metadata and geometry for a single physical monitor or projector."""
    index: int                  # 1-based index (Display 1, Display 2, ...)
    zero_index: int             # 0-based index for Qt screen indexing
    name: str                   # Windows / Device name
    x: int                      # Virtual desktop X coordinate (can be negative!)
    y: int                      # Virtual desktop Y coordinate (can be negative!)
    width: int                  # Width in pixels
    height: int                 # Height in pixels
    is_primary: bool            # True if this is the primary Windows display
    dpr: float                  # Device pixel ratio
    screen_obj: Optional[QScreen] = None

    @property
    def is_secondary(self) -> bool:
        """True if this is a secondary display or projector (not the Windows primary)."""
        return not self.is_primary

    @property
    def bounds(self) -> QRect:
        """Returns QRect representing the exact geometry bounds."""
        return QRect(self.x, self.y, self.width, self.height)

    @property
    def resolution_str(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def position_str(self) -> str:
        return f"{self.x},{self.y}"

    @property
    def role_str(self) -> str:
        return "Primary" if self.is_primary else "Secondary"

    def format_card(self) -> str:
        """Formatted summary block as requested in specifications."""
        return (
            f"[{self.index}] {self.role_str}\n"
            f"Resolution: {self.width} x {self.height}\n"
            f"Position: {self.x},{self.y}"
        )


class DisplayManager:
    """Detects and monitors Windows displays including projectors and multi-monitors."""

    @staticmethod
    def get_displays() -> List[DisplayInfo]:
        """
        Query Qt for all active displays.
        Handles multi-monitor configurations and negative virtual desktop coordinates.
        """
        app = QGuiApplication.instance()
        if not app:
            return []

        screens = QGuiApplication.screens()
        primary_screen = QGuiApplication.primaryScreen()
        display_list: List[DisplayInfo] = []

        for idx, screen in enumerate(screens):
            geom = screen.geometry()

            # Identify if this screen is the Windows primary display
            is_primary = False
            if primary_screen is not None:
                is_primary = (screen == primary_screen) or (screen.name() == primary_screen.name())
            elif idx == 0:
                is_primary = True

            name = screen.name() or f"Display {idx + 1}"
            model = screen.model() if hasattr(screen, "model") and screen.model() else ""
            display_name = f"{name} ({model})" if model else name

            info = DisplayInfo(
                index=idx + 1,          # 1-based display label
                zero_index=idx,
                name=display_name,
                x=geom.x(),             # May be negative in Windows desktop layout
                y=geom.y(),             # May be negative
                width=geom.width(),
                height=geom.height(),
                is_primary=is_primary,
                dpr=screen.devicePixelRatio(),
                screen_obj=screen,
            )
            display_list.append(info)

        return display_list

    @staticmethod
    def get_display_by_1based_index(index: int) -> Optional[DisplayInfo]:
        for disp in DisplayManager.get_displays():
            if disp.index == index:
                return disp
        return None

    @staticmethod
    def get_primary_display() -> Optional[DisplayInfo]:
        displays = DisplayManager.get_displays()
        for disp in displays:
            if disp.is_primary:
                return disp
        return displays[0] if displays else None

    @staticmethod
    def get_secondary_display() -> Optional[DisplayInfo]:
        """Returns the first non-primary display (e.g. secondary monitor or projector)."""
        displays = DisplayManager.get_displays()
        for disp in displays:
            if not disp.is_primary:
                return disp
        return None
