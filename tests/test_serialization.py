import unittest

import numpy as np
import pandas as pd

from xtquant_bridge.serialization import serialize_xtdata_result


class SerializationTests(unittest.TestCase):
    def test_serialize_dataframe(self) -> None:
        frame = pd.DataFrame({"close": [10.1, 10.2]})

        payload = serialize_xtdata_result(frame)

        self.assertEqual(payload["__type__"], "dataframe")
        self.assertEqual(payload["orient"], "split")
        self.assertEqual(payload["data"]["columns"], ["close"])

    def test_serialize_ndarray(self) -> None:
        payload = serialize_xtdata_result(np.array([1, 2, 3], dtype=np.int64))

        self.assertEqual(payload["__type__"], "ndarray")
        self.assertEqual(payload["dtype"], "int64")
        self.assertEqual(payload["data"], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
