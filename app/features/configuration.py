"""Configure discovered features from their own declared settings."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from . import FeatureDefinition, FeatureSetting, FeatureSpec, _validate_definition


def settings_catalog(definitions: Sequence[FeatureDefinition]) -> tuple[FeatureSetting, ...]:
    settings: dict[str, FeatureSetting] = {}
    for definition in definitions:
        for setting in definition.settings:
            previous = settings.get(setting.key)
            if previous is not None and previous.as_dict() != setting.as_dict():
                raise ValueError(f"conflicting declarations for setting {setting.key!r}")
            settings[setting.key] = setting
    return tuple(settings.values())


def configure_features(
    definitions: Sequence[FeatureDefinition], requested: Mapping[str, str | int]
) -> tuple[FeatureDefinition, ...]:
    values: dict[str, int] = {}
    for setting in settings_catalog(definitions):
        raw = requested.get(setting.key, setting.default)
        try:
            value = int(raw) if not isinstance(raw, bool) else -1
        except (TypeError, ValueError) as error:
            raise ValueError(f"{setting.key} must be an integer") from error
        if isinstance(raw, str) and str(value) != raw:
            raise ValueError(f"{setting.key} must be an integer")
        if not setting.minimum <= value <= setting.maximum:
            raise ValueError(
                f"{setting.key} must be between {setting.minimum} and {setting.maximum}"
            )
        values[setting.key] = value
    configured = tuple(
        definition.configure(values)
        if definition.configure is not None
        and any(values[setting.key] != setting.default for setting in definition.settings)
        else definition
        for definition in definitions
    )
    for original, definition in zip(definitions, configured):
        _validate_definition(definition)
        for setting in original.settings:
            recorded = [
                spec.parameters[name]
                for spec in definition.specs
                for name in setting.parameters
                if name in spec.parameters
            ]
            if not recorded or any(
                type(value) is not int or value != values[setting.key] for value in recorded
            ):
                raise ValueError(f"{setting.key} is not recorded consistently in feature metadata")
    names = [spec.name for definition in configured for spec in definition.specs]
    identifiers = [view.identifier for definition in configured for view in definition.views]
    feature_keys = [
        spec.selection_key or spec.name for definition in configured for spec in definition.specs
    ]
    view_keys = [
        view.selection_key or view.identifier
        for definition in configured
        for view in definition.views
    ]
    if any(
        len(entries) != len(set(entries))
        for entries in (names, identifiers, feature_keys, view_keys)
    ):
        raise ValueError("configured feature names, identifiers, and selection keys must be unique")
    return configured


def configure_stored_features(
    definitions: Sequence[FeatureDefinition], stored_specs: Sequence[FeatureSpec]
) -> tuple[FeatureDefinition, ...]:
    values: dict[str, int] = {}
    for definition in definitions:
        for setting in definition.settings:
            for default_spec in definition.specs:
                pattern = re.sub(r"\d+", lambda _: r"\d+", re.escape(default_spec.name))
                for stored_spec in stored_specs:
                    same_feature = (
                        stored_spec.selection_key == default_spec.selection_key
                        if stored_spec.selection_key is not None
                        and default_spec.selection_key is not None
                        else re.fullmatch(pattern, stored_spec.name) is not None
                    )
                    if not same_feature:
                        continue
                    parameter = next(
                        (name for name in setting.parameters if name in stored_spec.parameters),
                        None,
                    )
                    if parameter is None:
                        continue
                    raw = stored_spec.parameters[parameter]
                    if not isinstance(raw, int) or isinstance(raw, bool):
                        raise ValueError(f"stored {setting.key} must be an integer")
                    previous = values.get(setting.key)
                    if previous is not None and previous != raw:
                        raise ValueError(f"stored {setting.key} has conflicting values")
                    values[setting.key] = raw
    return configure_features(definitions, values)
