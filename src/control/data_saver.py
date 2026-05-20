import threading
import time
import queue
import csv
import os

class DataSaver(threading.Thread):
    def __init__(self, filename, flush_interval=1.0, batch_size=1000, save_continuously=True, final_filename=None):
        super().__init__()
        self.filename = filename
        self.flush_interval = flush_interval
        self.batch_size = batch_size
        self.save_continuously = save_continuously
        self.final_filename = final_filename
        self.queue = queue.Queue()
        self.stop_event = threading.Event()

        # Ensure directory exists
        os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)
        if self.final_filename:
            os.makedirs(os.path.dirname(os.path.abspath(final_filename)), exist_ok=True)

        self.headers_written = False
        if self.save_continuously and os.path.exists(filename):
            self.headers_written = True

        # Register atexit handler to ensure data is saved on crash/exit
        import atexit
        atexit.register(self.stop)

    def add_event(self, data: dict):
        """
        Add a dictionary of data to the save queue.
        Keys must remain consistent for CSV writing.
        """
        self.queue.put(data)

    def run(self):
        last_flush = time.time()
        buffer = []
        self.full_buffer = [] # Buffer for non-continuous mode or final backup if needed

        try:
            f = None
            writer = None

            if self.save_continuously:
                 f = open(self.filename, 'a', newline='')

            while True:
                # We continue looping if we haven't stopped OR if there's still data
                if self.stop_event.is_set() and self.queue.empty():
                        break

                try:
                    # If stopped, don't wait long (effectively drain mode)
                    timeout = 0.1 if not self.stop_event.is_set() else 0.0
                    item = self.queue.get(timeout=timeout)
                    buffer.append(item)
                    if not self.save_continuously:
                        self.full_buffer.append(item)
                except queue.Empty:
                    continue

                # Periodic or Batch Flush
                now = time.time()
                if (now - last_flush >= self.flush_interval) or (len(buffer) >= self.batch_size):
                    if buffer and self.save_continuously and f:
                        if writer is None:
                            keys = buffer[0].keys()
                            writer = csv.DictWriter(f, fieldnames=keys)
                            if not self.headers_written:
                                writer.writeheader()
                                self.headers_written = True
                                f.flush()
                                os.fsync(f.fileno())

                        writer.writerows(buffer)
                        f.flush()
                        os.fsync(f.fileno()) # Force write to disk for safety
                        buffer = []
                    last_flush = now

            # Final flush on exit
            if buffer and self.save_continuously and f:
                    if writer is None and buffer:
                        keys = buffer[0].keys()
                        writer = csv.DictWriter(f, fieldnames=keys)
                        if not self.headers_written:
                            writer.writeheader()
                            self.headers_written = True

                    if writer:
                        writer.writerows(buffer)
                        f.flush()
                        os.fsync(f.fileno())

            if f:
                f.close()

            # --- FINAL BACKUP SAVE ---
            if self.final_filename:
                print(f"[Saver] Writing final backup to {self.final_filename}...")
                if self.save_continuously:
                    # Robust copy
                    import shutil
                    # Ensure source exists (it should, as we just closed it)
                    if os.path.exists(self.filename):
                         shutil.copy2(self.filename, self.final_filename)
                    else:
                         print(f"[Saver] Warning: Source file {self.filename} missing for backup copy.")
                else:
                    # Write from memory buffer
                    if self.full_buffer:
                        keys = self.full_buffer[0].keys()
                        with open(self.final_filename, 'w', newline='') as ff:
                            writer = csv.DictWriter(ff, fieldnames=keys)
                            writer.writeheader()
                            writer.writerows(self.full_buffer)
                            ff.flush()
                            os.fsync(ff.fileno())
                    else:
                        # Create empty file
                        with open(self.final_filename, 'w') as ff:
                            pass

        except Exception as e:
            print(f"[Saver] Critical Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            print(f"[Saver] Thread stopped. File: {self.filename}")

    def stop(self):
        if not self.stop_event.is_set():
            self.stop_event.set()
            # If called from main thread, join. If called from atexit/signal, careful.
            if threading.current_thread() is not self:
                self.join(timeout=2.0)
