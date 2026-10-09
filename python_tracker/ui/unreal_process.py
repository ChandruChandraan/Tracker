import subprocess
import threading
import time
from pathlib import Path
from typing import Optional, Tuple, Set
import ctypes
from ctypes import wintypes

from core import config
from .display_manager import DisplayInfo

user32 = ctypes.windll.user32
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class UnrealManager:
    """
    Manages the lifecycle and display positioning of the packaged Unreal Engine application
    (FiringSim.exe / FiringSim-Win64-Shipping.exe).
    Strictly bound to this project's package directory; never manipulates or closes external processes or Unreal Editor.
    """

    def __init__(self, exe_path: str = config.UNREAL_EXE_PATH):
        self.exe_path: str = exe_path
        self._proc: Optional[subprocess.Popen] = None

    def _get_process_path(self, pid: int) -> Optional[str]:
        """Returns the full executable file path for a given process ID using Windows API."""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h_process = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h_process:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buffer))
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(h_process, 0, buffer, ctypes.byref(size)):
                return buffer.value
        finally:
            ctypes.windll.kernel32.CloseHandle(h_process)
        return None

    def _is_bound_project_process(self, pid: int, proc_path: Optional[str] = None) -> bool:
        """
        Determines whether a process is strictly bound to this project's packaged executable.
        Must be located inside the project's package directory and match FiringSim binaries.
        Never matches Unreal Editor (UnrealEditor.exe) or any other application.
        """
        if self._proc is not None and self._proc.pid == pid:
            return True

        path_str = proc_path or self._get_process_path(pid)
        if not path_str:
            return False

        path_lower = path_str.lower().replace("\\", "/")

        # Exclude Unreal Editor explicitly
        if "unrealeditor" in path_lower:
            return False

        # Target executable package directory
        target_exe = Path(self.exe_path).resolve()
        target_dir = str(target_exe.parent).lower().replace("\\", "/")

        # Verify executable is inside this project's package folder
        if path_lower.startswith(target_dir):
            file_name = Path(path_str).name.lower()
            if file_name in ("firingsim.exe", "firingsim-win64-shipping.exe"):
                return True

        return False

    def _get_bound_pids(self) -> Set[int]:
        """Returns set of process IDs that belong strictly to this project's packaged executable."""
        pids: Set[int] = set()
        if self._proc is not None and self._proc.poll() is None:
            pids.add(self._proc.pid)

        # Enumerate running processes via tasklist to discover bound FiringSim instances
        try:
            cmd = ["tasklist", "/FO", "CSV", "/NH"]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=2.0,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            lines = [line.strip().strip('"') for line in res.stdout.splitlines() if line.strip()]
            for line in lines:
                parts = [p.strip().strip('"') for p in line.split('","')]
                if len(parts) >= 2:
                    proc_name = parts[0].lower()
                    if proc_name in ("firingsim.exe", "firingsim-win64-shipping.exe"):
                        try:
                            proc_pid = int(parts[1])
                            if self._is_bound_project_process(proc_pid):
                                pids.add(proc_pid)
                        except (ValueError, TypeError):
                            pass
        except Exception:
            pass

        return pids

    def is_running(self) -> Tuple[bool, Optional[int]]:
        """
        Checks whether this project's FiringSim executable is actively running.
        Returns (is_running, pid).
        """
        if self._proc is not None and self._proc.poll() is None:
            return True, self._proc.pid

        bound_pids = self._get_bound_pids()
        if bound_pids:
            return True, next(iter(bound_pids))

        return False, None

    def launch(self, display: DisplayInfo) -> bool:
        """
        Launches Unreal Engine positioned on the specified display (secondary monitor).
        Arguments passed: -WinX=<x> -WinY=<y> -ResX=<w> -ResY=<h> -WINDOWED -NoVSync
        """
        exe_file = Path(self.exe_path)
        if not exe_file.exists():
            print(f"[UNREAL] Executable not found at: {self.exe_path}")
            return False

        running, pid = self.is_running()
        if running:
            print(f"[UNREAL] FiringSim is already running (PID: {pid}). Repositioning to target display...")
            self.move_to_display(display)
            return True

        args = [
            str(exe_file),
            f"-WinX={display.x}",
            f"-WinY={display.y}",
            f"-ResX={display.width}",
            f"-ResY={display.height}",
            "-WINDOWED",
            "-Borderless",
            "-NoVSync",
        ]

        print(f"[UNREAL] Launching: {' '.join(args)}")
        try:
            self._proc = subprocess.Popen(
                args,
                cwd=str(exe_file.parent),
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            # Spawn background monitor thread to ensure window lands on target display
            threading.Thread(
                target=self._ensure_window_placement_worker,
                args=(display,),
                daemon=True,
            ).start()
            return True
        except Exception as ex:
            print(f"[UNREAL] Failed to launch Unreal Engine: {ex}")
            return False

    def move_to_display(self, display: DisplayInfo):
        """Asynchronously repositions the Unreal window onto the target display."""
        threading.Thread(
            target=self._ensure_window_placement_worker,
            args=(display,),
            daemon=True,
        ).start()

    def _ensure_window_placement_worker(self, display: DisplayInfo, duration_seconds: float = 15.0):
        """
        Monitors for the Unreal Engine game window, strips window borders and title bar,
        and sizes it into true borderless fullscreen on the target display.
        """
        target_x = display.x
        target_y = display.y
        target_w = display.width
        target_h = display.height

        GWL_STYLE = -16
        WS_CAPTION = 0x00C00000
        WS_THICKFRAME = 0x00040000
        WS_MINIMIZEBOX = 0x00020000
        WS_MAXIMIZEBOX = 0x00010000
        WS_SYSMENU = 0x00080000
        WS_BORDER = 0x00800000
        WS_POPUP = 0x80000000

        SWP_FRAMECHANGED = 0x0020
        SWP_NOZORDER = 0x0004
        SWP_SHOWWINDOW = 0x0040

        start_time = time.time()
        positioned_hwnds = set()

        while time.time() - start_time < duration_seconds:
            time.sleep(0.4)
            hwnds = self._find_unreal_hwnds()
            for hwnd in hwnds:
                try:
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    curr_w = rect.right - rect.left
                    curr_h = rect.bottom - rect.top

                    # Check if already positioned correctly
                    needs_position = (
                        hwnd not in positioned_hwnds
                        or abs(rect.left - target_x) > 4
                        or abs(rect.top - target_y) > 4
                        or abs(curr_w - target_w) > 4
                        or abs(curr_h - target_h) > 4
                    )

                    if needs_position:
                        # Strip window decorations (title bar, borders, frames)
                        current_style = user32.GetWindowLongW(hwnd, GWL_STYLE)
                        new_style = (current_style & ~(WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU | WS_BORDER)) | WS_POPUP
                        user32.SetWindowLongW(hwnd, GWL_STYLE, new_style)

                        # Set position and size to exact bounds of target display
                        user32.SetWindowPos(
                            hwnd,
                            0,
                            target_x,
                            target_y,
                            target_w,
                            target_h,
                            SWP_FRAMECHANGED | SWP_NOZORDER | SWP_SHOWWINDOW,
                        )
                        user32.BringWindowToTop(hwnd)
                        positioned_hwnds.add(hwnd)
                        print(f"[UNREAL] Positioned Unreal window (HWND: {hwnd}) borderless fullscreen at {target_x},{target_y} ({target_w}x{target_h})")
                except Exception as ex:
                    print(f"[UNREAL] Window positioning error on HWND {hwnd}: {ex}")

    def _find_unreal_hwnds(self) -> list:
        """Finds visible top-level HWNDs strictly belonging to this project's bound FiringSim process."""
        found_hwnds = []
        bound_pids = self._get_bound_pids()
        if not bound_pids:
            return []

        def enum_cb(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value in bound_pids:
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    # Ignore tiny helper/tooltip windows
                    if (rect.right - rect.left) > 150 and (rect.bottom - rect.top) > 150:
                        found_hwnds.append(hwnd)
            return True

        cb = WNDENUMPROC(enum_cb)
        user32.EnumWindows(cb, 0)
        return found_hwnds

    def terminate(self):
        """Terminates ONLY the Unreal processes that are strictly bound to this project."""
        print("[UNREAL] Terminating project-bound Unreal Engine processes...")

        bound_pids = self._get_bound_pids()

        # 1. Send WM_CLOSE strictly to windows belonging to this project's bound PIDs
        try:
            hwnds = self._find_unreal_hwnds()
            for hwnd in hwnds:
                try:
                    user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
                except Exception:
                    pass
        except Exception:
            pass

        # 2. Terminate the directly launched subprocess if still active
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
            self._proc = None

        # 3. Terminate remaining bound project PIDs strictly by verified PID
        for pid in bound_pids:
            if self._is_bound_project_process(pid):
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        capture_output=True,
                        timeout=2.0,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except Exception:
                    pass

        print("[UNREAL] Project-bound Unreal Engine processes terminated.")
