import unittest

from states.trip_state import default_transport_options


class DefaultTransportOptionsTests(unittest.TestCase):
    def test_default_shape(self):
        result = default_transport_options()
        self.assertEqual(result, {"railway": [], "flight": {"outbound": [], "inbound": []}})

    def test_returns_new_object_each_call(self):
        a = default_transport_options()
        b = default_transport_options()
        a["railway"].append({"type": "train"})
        self.assertEqual(b["railway"], [])


if __name__ == "__main__":
    unittest.main()
