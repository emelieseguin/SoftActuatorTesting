"""Validated pressure calibration models and pure fitting functions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from numbers import Real

import numpy as np

from .artifacts import Unit
from .errors import CalibrationError, ErrorCode


class CalibrationModelType(str, Enum):
    LINEAR = "linear"
    QUADRATIC = "quadratic"


@dataclass(frozen=True)
class CalibrationSample:
    """A known pressure paired with the measured voltage that produced it."""

    known_pressure_kpa: float
    measured_voltage: float

    def __post_init__(self) -> None:
        _require_finite(self.known_pressure_kpa, "known_pressure_kpa")
        _require_finite(self.measured_voltage, "measured_voltage")


@dataclass(frozen=True)
class VoltageDomain:
    """Inclusive voltage interval in which a model was fitted and may be used."""

    minimum_volts: float
    maximum_volts: float

    def __post_init__(self) -> None:
        _require_finite(self.minimum_volts, "input_domain.minimum_volts")
        _require_finite(self.maximum_volts, "input_domain.maximum_volts")
        if self.minimum_volts > self.maximum_volts:
            raise CalibrationError(
                ErrorCode.CALIBRATION_INVALID,
                "minimum voltage must not exceed maximum voltage",
                "input_domain",
            )

    def contains(self, voltage: float) -> bool:
        return self.minimum_volts <= voltage <= self.maximum_volts


@dataclass(frozen=True)
class CalibrationModel:
    """Pressure (kPa) as a linear or quadratic function of voltage (V)."""

    model_type: CalibrationModelType
    coefficients: tuple[float, ...]
    input_domain: VoltageDomain | None = None
    input_unit: Unit = Unit.VOLT
    output_unit: Unit = Unit.KILOPASCAL

    def __post_init__(self) -> None:
        if not isinstance(self.model_type, CalibrationModelType):
            raise CalibrationError(
                ErrorCode.CALIBRATION_INVALID,
                "model_type must be linear or quadratic",
                "model.type",
            )
        expected = 2 if self.model_type is CalibrationModelType.LINEAR else 3
        if len(self.coefficients) != expected:
            raise CalibrationError(
                ErrorCode.CALIBRATION_INVALID,
                f"{self.model_type.value} models require exactly {expected} coefficients",
                "model.coeffs",
            )
        for index, coefficient in enumerate(self.coefficients):
            _require_finite(coefficient, f"model.coeffs[{index}]")

    def apply(self, voltage: float, *, require_in_domain: bool = True) -> float:
        _require_finite(voltage, "volts")
        if require_in_domain and self.input_domain and not self.input_domain.contains(voltage):
            raise CalibrationError(
                ErrorCode.CALIBRATION_INVALID,
                "voltage is outside the validated calibration domain",
                "volts",
                "Use a calibration covering this voltage or explicitly handle extrapolation.",
            )
        if self.model_type is CalibrationModelType.LINEAR:
            slope, intercept = self.coefficients
            value = slope * voltage + intercept
        else:
            quadratic, linear, intercept = self.coefficients
            value = quadratic * voltage**2 + linear * voltage + intercept
        _require_finite(value, "pressure_kpa")
        return value


@dataclass(frozen=True)
class FitAdequacy:
    """Objective fit metrics; policy layers decide acceptable error thresholds."""

    sample_count: int
    r_squared: float
    rmse_kpa: float
    is_adequate: bool
    reason: str | None = None
    condition_number: float = 0.0
    max_abs_residual_kpa: float = 0.0

    def __post_init__(self) -> None:
        if self.sample_count < 1:
            raise CalibrationError(ErrorCode.CALIBRATION_INVALID, "sample_count must be positive", "sample_count")
        _require_finite(self.r_squared, "fit.r_squared")
        _require_finite(self.rmse_kpa, "fit.rmse_kpa")
        if self.rmse_kpa < 0:
            raise CalibrationError(ErrorCode.CALIBRATION_INVALID, "rmse_kpa cannot be negative", "fit.rmse_kpa")
        _require_finite(self.condition_number, "fit.condition_number")
        _require_finite(self.max_abs_residual_kpa, "fit.max_abs_residual_kpa")


@dataclass(frozen=True)
class FitResidual:
    """Observed-minus-predicted pressure for one calibration sample."""

    sample_index: int
    voltage: float
    observed_pressure_kpa: float
    predicted_pressure_kpa: float
    residual_kpa: float


@dataclass(frozen=True)
class FitQualityPolicy:
    """Explicit, configurable fit-quality policy for operator-facing workflows."""

    minimum_r_squared: float | None = 0.98
    maximum_rmse_kpa: float | None = None
    maximum_condition_number: float = 1.0e8

    def __post_init__(self) -> None:
        for name, value in (
            ("minimum_r_squared", self.minimum_r_squared),
            ("maximum_rmse_kpa", self.maximum_rmse_kpa),
            ("maximum_condition_number", self.maximum_condition_number),
        ):
            if value is not None:
                _require_finite(value, f"fit_quality.{name}")
        if self.maximum_condition_number <= 1:
            raise CalibrationError(
                ErrorCode.CALIBRATION_INVALID,
                "maximum condition number must exceed one",
                "fit_quality.maximum_condition_number",
            )
        if self.maximum_rmse_kpa is not None and self.maximum_rmse_kpa < 0:
            raise CalibrationError(ErrorCode.CALIBRATION_INVALID, "maximum RMSE cannot be negative", "fit_quality.maximum_rmse_kpa")


@dataclass(frozen=True)
class CalibrationFit:
    model: CalibrationModel
    adequacy: FitAdequacy
    residuals: tuple[FitResidual, ...] = ()


def fit_calibration(
    samples: Sequence[CalibrationSample],
    model_type: CalibrationModelType,
    *,
    quality_policy: FitQualityPolicy | None = None,
) -> CalibrationFit:
    """Fit pressure as a function of voltage with rank and finite-value checks."""

    required_samples = 2 if model_type is CalibrationModelType.LINEAR else 3
    if len(samples) < required_samples:
        raise CalibrationError(
            ErrorCode.CALIBRATION_INVALID,
            f"{model_type.value} fitting requires at least {required_samples} samples",
            "samples",
        )
    volts = np.asarray([sample.measured_voltage for sample in samples], dtype=float)
    pressures = np.asarray([sample.known_pressure_kpa for sample in samples], dtype=float)
    if not np.isfinite(volts).all() or not np.isfinite(pressures).all():
        raise CalibrationError(ErrorCode.NON_FINITE_VALUE, "samples must be finite", "samples")
    degree = 1 if model_type is CalibrationModelType.LINEAR else 2
    if np.unique(volts).size < degree + 1:
        raise CalibrationError(
            ErrorCode.CALIBRATION_INVALID,
            f"{model_type.value} fitting requires at least {degree + 1} distinct voltages",
            "samples",
        )

    policy = quality_policy or FitQualityPolicy()
    design = np.vander(volts, degree + 1)
    condition_number = float(np.linalg.cond(design))
    if not np.isfinite(condition_number) or condition_number > policy.maximum_condition_number:
        raise CalibrationError(
            ErrorCode.CALIBRATION_INVALID,
            "calibration samples are poorly conditioned for this fit",
            "samples",
            "Spread distinct voltage samples farther apart or use a lower-order model.",
        )
    coefficients, _, rank, _ = np.linalg.lstsq(design, pressures, rcond=None)
    if rank < degree + 1 or not np.isfinite(coefficients).all():
        raise CalibrationError(
            ErrorCode.CALIBRATION_INVALID,
            "calibration samples do not support a full-rank fit",
            "samples",
        )
    predicted = design @ coefficients
    residuals = pressures - predicted
    rmse = float(np.sqrt(np.mean(residuals**2)))
    total_sum_squares = float(np.sum((pressures - np.mean(pressures)) ** 2))
    residual_sum_squares = float(np.sum(residuals**2))
    r_squared = (
        1.0
        if total_sum_squares == 0 and residual_sum_squares == 0
        else 0.0
        if total_sum_squares == 0
        else float(1 - residual_sum_squares / total_sum_squares)
    )
    reasons: list[str] = []
    if policy.minimum_r_squared is not None and r_squared < policy.minimum_r_squared:
        reasons.append(f"R² {r_squared:.3f} is below the required {policy.minimum_r_squared:.3f}")
    if policy.maximum_rmse_kpa is not None and rmse > policy.maximum_rmse_kpa:
        reasons.append(f"RMSE {rmse:.3f} kPa exceeds {policy.maximum_rmse_kpa:.3f} kPa")
    model = CalibrationModel(
        model_type=model_type,
        coefficients=tuple(float(value) for value in coefficients),
        input_domain=VoltageDomain(float(np.min(volts)), float(np.max(volts))),
    )
    return CalibrationFit(
        model,
        FitAdequacy(
            len(samples),
            r_squared,
            rmse,
            is_adequate=not reasons,
            reason="; ".join(reasons) or None,
            condition_number=condition_number,
            max_abs_residual_kpa=float(np.max(np.abs(residuals))),
        ),
        tuple(
            FitResidual(
                sample_index=index,
                voltage=float(volts[index]),
                observed_pressure_kpa=float(pressures[index]),
                predicted_pressure_kpa=float(predicted[index]),
                residual_kpa=float(residuals[index]),
            )
            for index in range(len(samples))
        ),
    )


def apply_calibration(model: CalibrationModel, voltage: float) -> float:
    """Apply a validated calibration model using its declared input domain."""

    return model.apply(voltage)


def _require_finite(value: float, field_path: str) -> None:
    if not isinstance(value, Real) or isinstance(value, bool) or not isfinite(value):
        raise CalibrationError(
            ErrorCode.NON_FINITE_VALUE,
            "value must be finite",
            field_path,
        )
