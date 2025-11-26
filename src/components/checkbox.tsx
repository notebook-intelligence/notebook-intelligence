// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React from 'react';

import { MdOutlineCheckBoxOutlineBlank, MdCheckBox } from 'react-icons/md';

export function CheckBoxItem(props: any) {
  const indent = props.indent || 0;

  return (
    <div
      className={`checkbox-item checkbox-item-indent-${indent} ${props.header ? 'checkbox-item-header' : ''}`}
      title={props.title}
      onClick={event => props.onClick(event)}
    >
      <div className="checkbox-item-toggle">
        {props.checked ? (
          <MdCheckBox className="checkbox-icon" />
        ) : (
          <MdOutlineCheckBoxOutlineBlank className="checkbox-icon" />
        )}
        {props.label}
      </div>
      {props.title && (
        <div className="checkbox-item-description">{props.title}</div>
      )}
    </div>
  );
}
