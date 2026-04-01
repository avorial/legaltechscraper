import re

with open(r"C:\Users\patricthomas\Desktop\legaltechnewsscraper\ui.py", "r", encoding="utf-8") as f:
    content = f.read()

# Remove the hint label creation block
content = content.replace(
    '''        self._segment_var.trace_add("write", self._on_segment_change)

        # Segment hint label — updates when segment changes
        self._segment_hint = tk.Label(
            ctrl_frame, text="",
            bg=CARD_BG, fg=ACCENT, font=("Segoe UI", 8, "italic"),
        )
        self._segment_hint.grid(row=0, column=0, columnspan=3, sticky="e", padx=14)
        self._on_segment_change()  # set initial hint''',
    '''        self._segment_var.trace_add("write", self._on_segment_change)
        self._on_segment_change()  # set initial button color'''
)

# Remove the hint update inside _on_segment_change
content = content.replace(
    '''        hints = {
            "Strategic":     "Standard scoring — balanced across all firm types",
            "SML":           "Boosts: practice management, small-firm pain, billing signals",
            "International": "Boosts: UK, EU, APAC, Canada, cross-border content",
        }
        if hasattr(self, "_segment_hint"):
            self._segment_hint.configure(text=hints.get(seg, ""), fg=color)''',
    ''
)

with open(r"C:\Users\patricthomas\Desktop\legaltechnewsscraper\ui.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Done")
