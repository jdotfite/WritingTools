import logging
import threading

from pynput import keyboard as pykeyboard


class AutoCorrectMode:
    """
    Auto-correct mode that captures keystrokes into a buffer and automatically
    sends them for proofreading after a pause in typing.
    """

    def __init__(self, app):
        self.app = app
        self.enabled = False
        self.buffer = []
        self.debounce_timer = None
        self.is_correcting = False
        self.PAUSE_DELAY = 2.0
        self.MIN_BUFFER_LENGTH = 2

    def toggle(self):
        """Toggle auto-correct mode on/off."""
        self.enabled = not self.enabled
        self.buffer.clear()
        if self.debounce_timer:
            self.debounce_timer.cancel()
            self.debounce_timer = None
        logging.info(f'Auto-correct mode {"ON" if self.enabled else "OFF"}')
        self.app.autocorrect_toggled_signal.emit(self.enabled)

    def on_key_press(self, key):
        """Called from the pynput Listener's on_press handler."""
        if not self.enabled or self.is_correcting:
            return

        # Reset debounce timer on every keypress
        if self.debounce_timer:
            self.debounce_timer.cancel()
            self.debounce_timer = None

        # Buffer management
        if hasattr(key, 'char') and key.char:
            self.buffer.append(key.char)
        elif key == pykeyboard.Key.space:
            self.buffer.append(' ')
        elif key == pykeyboard.Key.backspace:
            if self.buffer:
                self.buffer.pop()
        elif key == pykeyboard.Key.enter:
            if len(self.buffer) >= self.MIN_BUFFER_LENGTH:
                self.trigger_correction()
            self.buffer.clear()
            return
        else:
            # Arrow keys, tab, etc. = reset buffer (cursor position unknown)
            self.buffer.clear()
            return

        # Start/restart the debounce timer
        if len(self.buffer) >= self.MIN_BUFFER_LENGTH:
            self.debounce_timer = threading.Timer(self.PAUSE_DELAY, self.trigger_correction)
            self.debounce_timer.daemon = True
            self.debounce_timer.start()

    def trigger_correction(self):
        """Send buffer to LLM for proofreading, then replace typed text."""
        if not self.buffer or not self.enabled:
            return

        original_text = ''.join(self.buffer)
        buffer_len = len(self.buffer)
        self.buffer.clear()

        # Load proofread config from options.json
        proofread_option = self.app.options.get('Proofread', {})
        prefix = proofread_option.get('prefix', 'Proofread this:\n\n')
        instruction = proofread_option.get('instruction', '')

        prompt = prefix + original_text

        # Call AI provider (blocking, runs in timer thread)
        try:
            response = self.app.current_provider.get_response(instruction, prompt, return_response=True)
        except Exception as e:
            logging.error(f'Auto-correct error: {e}')
            return

        if not response or 'ERROR_TEXT_INCOMPATIBLE' in response:
            return

        corrected_text = response.strip()

        # If no changes, do nothing
        if corrected_text == original_text:
            return

        logging.debug(f'Auto-correct: "{original_text}" -> "{corrected_text}"')

        # Replace: backspace out the old text, type the new text
        self.is_correcting = True
        try:
            kbrd = pykeyboard.Controller()
            for _ in range(buffer_len):
                kbrd.press(pykeyboard.Key.backspace)
                kbrd.release(pykeyboard.Key.backspace)
            kbrd.type(corrected_text)
        except Exception as e:
            logging.error(f'Auto-correct replacement error: {e}')
        finally:
            self.is_correcting = False
