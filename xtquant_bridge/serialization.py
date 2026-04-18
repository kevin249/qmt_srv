from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd


def serialize_xtdata_result(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__type__": "date", "value": value.isoformat()}
    if isinstance(value, pd.DataFrame):
        return {
            "__type__": "dataframe",
            "orient": "split",
            "data": value.to_dict(orient="split"),
        }
    if isinstance(value, np.ndarray):
        return {
            "__type__": "ndarray",
            "dtype": str(value.dtype),
            "data": value.tolist(),
        }
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): serialize_xtdata_result(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize_xtdata_result(item) for item in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return serialize_xtdata_result(value.to_dict())
    return value
