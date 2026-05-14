import customtkinter as ctk
import os
from PIL import Image

# Initialize the UI Theme
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class ScrollableImageFrame(ctk.CTkScrollableFrame):
    """A scrolling frame to hold dynamic pipeline steps/images."""
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.widgets = []
        self._image_refs = []  # prevent garbage collection
        self._scan_animations = {}  # track active scanning animations

    def add_step(self, title_text, image_path=None, description_text=None, scanning=False):
        """Adds a phase block to the scrollable frame."""
        frame = ctk.CTkFrame(self, corner_radius=12, fg_color=("#e8edf2", "#1e2a3a"))
        frame.grid(row=len(self.widgets), column=0, pady=8, padx=8, sticky="ew")
        frame.grid_columnconfigure(0, weight=1)

        # ── Title with phase indicator ──
        if scanning:
            title_color = ("#d4a017", "#f1c40f")  # gold for scanning
            dot = "🔍"
        elif "✅" in title_text or "Done" in title_text:
            title_color = ("#27ae60", "#2ecc71")  # green for done
            dot = ""
        elif "❌" in title_text or "Error" in title_text:
            title_color = ("#c0392b", "#e74c3c")  # red for error
            dot = ""
        else:
            title_color = ("#1a73e8", "#4fc3f7")  # blue default
            dot = "●"

        title_display = f"{dot} {title_text}" if dot and dot not in title_text else title_text
        title_lbl = ctk.CTkLabel(
            frame, text=title_display,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=title_color
        )
        title_lbl.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        # ── Description ──
        if description_text:
            desc_lbl = ctk.CTkLabel(
                frame, text=description_text,
                wraplength=450, justify="left",
                font=ctk.CTkFont(size=12),
                text_color=("gray40", "gray70")
            )
            desc_lbl.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 6))

        # ── Image ──
        if image_path and os.path.exists(image_path):
            try:
                pil_img = Image.open(image_path)
                max_width = 420
                ratio = max_width / pil_img.width
                new_h = int(pil_img.height * ratio)
                new_size = (max_width, new_h)

                ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=new_size)
                self._image_refs.append(ctk_img)

                # Container for overlays
                img_container = ctk.CTkFrame(frame, fg_color="transparent")
                img_container.grid(row=2, column=0, pady=(4, 10), padx=12)

                img_lbl = ctk.CTkLabel(img_container, image=ctk_img, text="")
                img_lbl.pack()

                # ── Scanning overlay ──
                if scanning:
                    # Scanning bar animation
                    scan_bar = ctk.CTkProgressBar(
                        img_container, width=max_width,
                        height=6, corner_radius=3,
                        mode="indeterminate",
                        progress_color=("#f1c40f", "#f39c12")
                    )
                    scan_bar.pack(pady=(2, 0))
                    scan_bar.start()

                    scan_label = ctk.CTkLabel(
                        img_container,
                        text="🤖 AI is analyzing this photo...",
                        font=ctk.CTkFont(size=12, weight="bold"),
                        text_color=("#d4a017", "#f1c40f")
                    )
                    scan_label.pack(pady=(4, 0))

                    # Store for cleanup
                    frame_id = id(frame)
                    self._scan_animations[frame_id] = scan_bar

            except Exception as e:
                err_lbl = ctk.CTkLabel(frame, text=f"⚠ Image error: {e}", text_color="red")
                err_lbl.grid(row=2, column=0, pady=6)

        self.widgets.append(frame)

        # Auto-scroll to bottom
        try:
            self.after(100, lambda: self._parent_canvas.yview_moveto(1.0))
        except Exception:
            pass

    def clear_steps(self):
        """Removes all step widgets to reset for a new row."""
        # Stop any running animations
        for bar in self._scan_animations.values():
            try:
                bar.stop()
            except Exception:
                pass
        self._scan_animations.clear()

        for widget in self.widgets:
            widget.destroy()
        self.widgets.clear()
        self._image_refs.clear()
