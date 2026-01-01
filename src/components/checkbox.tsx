// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React from 'react';

import { MdOutlineCheckBoxOutlineBlank, MdCheckBox } from 'react-icons/md';

export function CheckBoxItem(props: any) {
  const indent = props.indent || 0;
  const disabled = props.disabled || false;

  return (
    <div
      className={`checkbox-item checkbox-item-indent-${indent} ${props.header ? 'checkbox-item-header' : ''}`}
      title={props.tooltip || props.title || ''}
      onClick={event => {
        if (!disabled) {
          props.onClick(event);
        }
      }}
    >
      <div className="checkbox-item-toggle">
        {props.checked ? (
          <MdCheckBox
            className="checkbox-icon"
            style={{ opacity: disabled ? 0.5 : 1 }}
          />
        ) : (
          <MdOutlineCheckBoxOutlineBlank
            className="checkbox-icon"
            style={{ opacity: disabled ? 0.5 : 1 }}
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
