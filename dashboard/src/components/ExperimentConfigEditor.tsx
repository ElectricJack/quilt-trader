import { useState, useEffect } from "react";
import { JsonTextField } from "./JsonTextField";

interface Props {
  baseConfig: Record<string, unknown> | null;
  parameterSpace: Record<string, unknown> | null;
  criteria: Record<string, unknown> | null;
  onChange: (next: {
    base_config: Record<string, unknown> | null;
    parameter_space: Record<string, unknown> | null;
    pre_registered_criteria: Record<string, unknown> | null;
  }) => void;
  onValidityChange?: (allValid: boolean) => void;
  disabled?: boolean;
}

export function ExperimentConfigEditor({
  baseConfig, parameterSpace, criteria,
  onChange, onValidityChange, disabled,
}: Props) {
  const [base, setBase] = useState(baseConfig);
  const [ps, setPs] = useState(parameterSpace);
  const [crit, setCrit] = useState(criteria);
  const [baseValid, setBaseValid] = useState(true);
  const [psValid, setPsValid] = useState(true);
  const [critValid, setCritValid] = useState(true);

  useEffect(() => setBase(baseConfig), [baseConfig]);
  useEffect(() => setPs(parameterSpace), [parameterSpace]);
  useEffect(() => setCrit(criteria), [criteria]);

  useEffect(() => {
    onChange({
      base_config: base,
      parameter_space: ps,
      pre_registered_criteria: crit,
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, ps, crit]);

  useEffect(() => {
    onValidityChange?.(baseValid && psValid && critValid);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseValid, psValid, critValid]);

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      <JsonTextField
        label="Base config"
        value={base}
        onChange={setBase}
        onError={(hasErr) => setBaseValid(!hasErr)}
        disabled={disabled}
        placeholder='{"vol_target": 0.10}'
      />
      <JsonTextField
        label="Parameter space"
        value={ps}
        onChange={setPs}
        onError={(hasErr) => setPsValid(!hasErr)}
        disabled={disabled}
        placeholder='{"lookback": [20, 50, 100]}'
      />
      <JsonTextField
        label="Pre-registered criteria"
        value={crit}
        onChange={setCrit}
        onError={(hasErr) => setCritValid(!hasErr)}
        disabled={disabled}
        placeholder='{"min_sharpe": 1.0}'
      />
    </div>
  );
}
