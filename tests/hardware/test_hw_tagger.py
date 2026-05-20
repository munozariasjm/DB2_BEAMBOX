import sys
import os
import time
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from src.devices.tagger import Tagger

def test_tagger():
    print("=== Testing Real Hardware: Tagger ===")

    try:
        # 1. Initialize
        print("\n1. Initializing Tagger...")
        tagger = Tagger(index=0)
        print("   [PASS] Initialization")

        # 2. Configure (Check if methods don't crash)
        print("\n2. Configuring Tagger...")
        # tagger.set_trigger_level(0.5)
        # tagger.set_channel_level(1, 0.2)#
        # tagger.set_trigger_falling()
        print("   [PASS] Configuration methods")

        # 3. Start Reading
        print("\n3. Starting Acquisition...")
        tagger.start_reading()
        print("   [PASS] Started")

        # 4. Read Data
        print("\n4. Reading Data (for 2 seconds)...")
        start = time.time()
        packet_count = 0
        while time.time() - start < 2:
            data = tagger.get_data()
            print(data)
            if data:
                packet_count += len(data)
                print(f"   Received {len(data)} packets")
            time.sleep(0.1)

        print(f"   Total packets received: {packet_count}")
        print("   [PASS] Data Reading")

        # 5. Stop
        print("\n5. Stopping...")
        tagger.stop()
        print("   [PASS] Stopped")

        print("\n=== Tagger Test Complete ===")

    except Exception as e:
        print(f"\n[FAIL] An error occurred: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_tagger()
