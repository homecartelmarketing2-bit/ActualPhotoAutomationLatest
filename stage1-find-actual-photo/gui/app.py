import sys
import os
import threading
import time
import traceback
import customtkinter as ctk
from PIL import Image

# Ensure project root is in path (go up one level from gui/)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from config.tables import AIRTABLE_TABLES
from config.settings import IDLE_WAIT_SECONDS
from gui.components import ScrollableImageFrame


class ContentCreationApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("HomeCartel Automation Marketing Content")
        self.geometry("1050x750")
        self.minsize(900, 600)

        # Configure layout
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main_view()

        # ── Redirect stdout/stderr to log panel ──
        # In --windowed PyInstaller, sys.stdout can be None
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = self
        sys.stderr = self

        # State flags
        self.is_running = False
        self.stop_event = threading.Event()
        self.pipeline_thread = None

    # ── stdout/stderr redirect ───────────────────────────────────
    def write(self, text):
        if text and text.strip():
            try:
                self.after(0, lambda t=text: self._append_log(t))
            except Exception:
                pass
        # Also write to original stdout if it exists (for console debugging)
        if self._original_stdout is not None:
            try:
                self._original_stdout.write(text)
            except Exception:
                pass

    def flush(self):
        if self._original_stdout is not None:
            try:
                self._original_stdout.flush()
            except Exception:
                pass

    def _append_log(self, text):
        try:
            self.log_textbox.configure(state="normal")
            self.log_textbox.insert("end", text.strip() + "\n")
            self.log_textbox.see("end")
            self.log_textbox.configure(state="disabled")
        except Exception:
            pass

    # ── Sidebar ──────────────────────────────────────────────────
    def _build_sidebar(self):
        self.sidebar_frame = ctk.CTkFrame(self, width=260, corner_radius=0,
                                           fg_color=("#d6e4f0", "#1a1a2e"))
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(6, weight=1)
        self.sidebar_frame.grid_columnconfigure(0, weight=1)

        logo_path = os.path.join(_PROJECT_ROOT, "logo.png")
        self._logo_image = ctk.CTkImage(
            light_image=Image.open(logo_path),
            dark_image=Image.open(logo_path),
            size=(180, 40)
        )
        self.logo_label = ctk.CTkLabel(
            self.sidebar_frame, image=self._logo_image, text=""
        )
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 5))

        self.subtitle = ctk.CTkLabel(
            self.sidebar_frame, text="Marketing Content Automation",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60")
        )
        self.subtitle.grid(row=1, column=0, padx=20, pady=(0, 15))

        self.table_lbl = ctk.CTkLabel(self.sidebar_frame, text="Table: —", anchor="w", font=ctk.CTkFont(size=12))
        self.table_lbl.grid(row=2, column=0, padx=20, pady=4, sticky="ew")

        self.record_lbl = ctk.CTkLabel(self.sidebar_frame, text="Record: —", anchor="w", font=ctk.CTkFont(size=12))
        self.record_lbl.grid(row=3, column=0, padx=20, pady=4, sticky="ew")

        self.phase_lbl = ctk.CTkLabel(self.sidebar_frame, text="Phase: —", anchor="w", font=ctk.CTkFont(size=12))
        self.phase_lbl.grid(row=4, column=0, padx=20, pady=4, sticky="ew")

        self.status_lbl = ctk.CTkLabel(
            self.sidebar_frame, text="⏸ IDLE",
            text_color=("orange", "yellow"),
            font=ctk.CTkFont(size=14, weight="bold"), anchor="w"
        )
        self.status_lbl.grid(row=5, column=0, padx=20, pady=(10, 5), sticky="ew")

        self.start_btn = ctk.CTkButton(
            self.sidebar_frame, text="▶  Start Automation",
            command=self.toggle_automation,
            height=40, corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.start_btn.grid(row=7, column=0, padx=20, pady=(10, 20), sticky="ew")

    # ── Main View ────────────────────────────────────────────────
    def _build_main_view(self):
        self.main_frame = ctk.CTkFrame(self, corner_radius=10)
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=(0, 10), pady=10)
        self.main_frame.grid_rowconfigure(1, weight=3)
        self.main_frame.grid_rowconfigure(3, weight=1)
        self.main_frame.grid_columnconfigure(0, weight=1)

        self.header_lbl = ctk.CTkLabel(
            self.main_frame, text="📋  Pipeline Visualization",
            font=ctk.CTkFont(size=16, weight="bold"), anchor="w"
        )
        self.header_lbl.grid(row=0, column=0, pady=(10, 5), padx=15, sticky="w")

        self.pipeline_view = ScrollableImageFrame(self.main_frame)
        self.pipeline_view.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 5))

        # ── Log toggle header ──
        self.log_header_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.log_header_frame.grid(row=2, column=0, pady=(5, 2), padx=15, sticky="ew")
        self.log_header_frame.grid_columnconfigure(0, weight=1)

        log_header = ctk.CTkLabel(
            self.log_header_frame, text="📝  Activity Log",
            font=ctk.CTkFont(size=14, weight="bold"), anchor="w"
        )
        log_header.grid(row=0, column=0, sticky="w")

        self.log_visible = True
        self.log_toggle_btn = ctk.CTkButton(
            self.log_header_frame, text="Hide ▲", width=70, height=26,
            corner_radius=6, font=ctk.CTkFont(size=12),
            fg_color=("gray75", "gray30"), hover_color=("gray65", "gray40"),
            command=self._toggle_log
        )
        self.log_toggle_btn.grid(row=0, column=1, sticky="e")

        self.log_textbox = ctk.CTkTextbox(
            self.main_frame, height=140, state="disabled",
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=("#f5f5f5", "#0d1117"),
            text_color=("#333333", "#c9d1d9"),
            corner_radius=8
        )
        self.log_textbox.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))

    # ── Log Toggle ────────────────────────────────────────────────
    def _toggle_log(self):
        if self.log_visible:
            self.log_textbox.grid_remove()
            self.main_frame.grid_rowconfigure(3, weight=0)
            self.main_frame.grid_rowconfigure(1, weight=1)
            self.log_toggle_btn.configure(text="Show ▼")
        else:
            self.log_textbox.grid()
            self.main_frame.grid_rowconfigure(3, weight=1)
            self.main_frame.grid_rowconfigure(1, weight=3)
            self.log_toggle_btn.configure(text="Hide ▲")
        self.log_visible = not self.log_visible

    # ── Controls ─────────────────────────────────────────────────
    def toggle_automation(self):
        if not self.is_running:
            self.is_running = True
            self.stop_event.clear()
            self.start_btn.configure(text="⏹  Stop Automation", fg_color="#c0392b", hover_color="#922b21")
            self.status_lbl.configure(text="▶ RUNNING", text_color=("#27ae60", "#2ecc71"))
            self.pipeline_thread = threading.Thread(target=self.run_pipeline_loop, daemon=True)
            self.pipeline_thread.start()
        else:
            self.is_running = False
            self.stop_event.set()
            self.start_btn.configure(
                text="▶  Start Automation",
                fg_color=["#3a7ebf", "#1f538d"],
                hover_color=["#325882", "#14375e"]
            )
            self.status_lbl.configure(text="⏳ STOPPING...", text_color="orange")

    # ── Pipeline Loop ────────────────────────────────────────────
    def run_pipeline_loop(self):
        try:
            from pipeline import process_one_row
        except Exception as e:
            self._log_error(f"Failed to import pipeline: {e}")
            self._safe_update_sidebar(status="❌ IMPORT ERROR", status_color="red")
            return

        while self.is_running:
            try:
                total_actions = 0

                for table in AIRTABLE_TABLES:
                    if not self.is_running:
                        break

                    table_name = table["name"]
                    table_id = table["id"]
                    blend_prompt = table["blend_prompt"]

                    self._safe_update_sidebar(
                        status="▶ RUNNING",
                        table_name=table_name,
                        record_id="Scanning...",
                        phase="Looking for rows...",
                        status_color=("#27ae60", "#2ecc71")
                    )

                    self.clear_pipeline_view()
                    self.add_pipeline_step(
                        f"🔍 Scanning table: {table_name}",
                        description_text="Looking for the next unfinished row..."
                    )

                    try:
                        if process_one_row(table_id, blend_prompt, ui_callback=self._ui_callback, stop_event=self.stop_event):
                            total_actions += 1
                    except Exception as e:
                        self._log_error(f"Error processing table '{table_name}': {e}\n{traceback.format_exc()}")
                        self.add_pipeline_step(
                            f"❌ Error in table: {table_name}",
                            description_text=str(e)
                        )

                if total_actions == 0 and self.is_running:
                    self._safe_update_sidebar(
                        status=f"💤 Idle ({IDLE_WAIT_SECONDS}s)",
                        table_name="—", record_id="—", phase="Waiting...",
                        status_color=("orange", "yellow")
                    )
                    self.clear_pipeline_view()
                    self.add_pipeline_step(
                        "💤 All tables clear",
                        description_text=f"No pending rows. Waiting {IDLE_WAIT_SECONDS}s..."
                    )
                    for _ in range(IDLE_WAIT_SECONDS):
                        if self.stop_event.is_set():
                            break
                        self.stop_event.wait(1)
                else:
                    self._safe_update_sidebar(
                        status=f"▶ Cycle done ({total_actions} rows)",
                        status_color=("#27ae60", "#2ecc71")
                    )

            except Exception as e:
                self._log_error(f"Pipeline loop error: {e}\n{traceback.format_exc()}")
                time.sleep(5)

        self._safe_update_sidebar(
            status="⏸ STOPPED", table_name="—", record_id="—", phase="—",
            status_color=("orange", "yellow")
        )

    # ── UI Callback (called by pipeline.py) ──────────────────────
    def _ui_callback(self, title, image_path=None, desc_text=None, scanning=False):
        self._safe_update_phase(title)
        self.add_pipeline_step(title, image_path=image_path, description_text=desc_text, scanning=scanning)

    # ── Thread-safe helpers ──────────────────────────────────────
    def _safe_update_sidebar(self, status, table_name=None, record_id=None,
                              phase=None, status_color=None):
        def _do():
            try:
                self.status_lbl.configure(text=status)
                if status_color:
                    self.status_lbl.configure(text_color=status_color)
                if table_name is not None:
                    self.table_lbl.configure(text=f"Table: {table_name}")
                if record_id is not None:
                    self.record_lbl.configure(text=f"Record: {record_id}")
                if phase is not None:
                    self.phase_lbl.configure(text=f"Phase: {phase}")
            except Exception:
                pass
        self.after(0, _do)

    def _safe_update_phase(self, phase_text):
        self.after(0, lambda: self.phase_lbl.configure(text=f"Phase: {phase_text}"))

    def add_pipeline_step(self, title, image_path=None, description_text=None, scanning=False):
        self.after(0, lambda: self.pipeline_view.add_step(title, image_path, description_text, scanning=scanning))

    def clear_pipeline_view(self):
        self.after(0, self.pipeline_view.clear_steps)

    def _log_error(self, msg):
        """Log an error to the activity log even if print is broken."""
        self.after(0, lambda: self._append_log(f"[ERROR] {msg}"))


if __name__ == "__main__":
    app = ContentCreationApp()
    app.mainloop()
