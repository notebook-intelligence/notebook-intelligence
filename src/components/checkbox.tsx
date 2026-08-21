// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React from 'react';

import { MdOutlineCheckBoxOutlineBlank, MdCheckBox } from '../icons';

export function CheckBoxItem(props: any) {
  const indent = props.indent || 0;
  const disabled = props.disabled || false;
  const checked = !!props.checked;

  const activate = (event: React.SyntheticEvent) => {
    if (!disabled) {
      props.onClick(event);
    }
  };

  // Custom checkbox widget. Tracks the WAI-ARIA pattern: role='checkbox'
  // + aria-checked + tabIndex=0 + Space/Enter to toggle. Disabled state
  // is exposed via aria-disabled so the element stays focusable; a
  // screen reader user can still announce "checkbox, disabled" and hear
  // what's off without being able to toggle it.
  return (
    <div
      className={`checkbox-item checkbox-item-indent-${indent} ${props.header ? 'checkbox-item-header' : ''}`}
      title={props.tooltip || props.title || ''}
      role="checkbox"
      aria-checked={checked}
      aria-disabled={disabled || undefined}
      aria-describedby={props.ariaDescribedBy}
      tabIndex={disabled ? -1 : 0}
      onClick={activate}
      onKeyDown={event => {
        if (event.key === ' ' || event.key === 'Enter') {
          // Space scrolls the page by default, Enter is a no-op on a
          // <div>; prevent both so the checkbox can own them.
          event.preventDefault();
          activate(event);
        }
      }}
    >
      <div className="checkbox-item-toggle">
        {checked ? (
          <MdCheckBox
            className="checkbox-icon"
            style={{ opacity: disabled ? 0.5 : 1 }}
            aria-hidden="true"
          />
        ) : (
          <MdOutlineCheckBoxOutlineBlank
            className="checkbox-icon"
            style={{ opacity: disabled ? 0.5 : 1 }}
            aria-hidden="true"
          />
        )}
        <span style={{ opacity: disabled ? 0.5 : 1 }}>{props.label}</span>
      </div>
      {props.title && (
        <div className="checkbox-item-description">{props.title}</div>
      )}
    </div>
  );
}
