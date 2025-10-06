// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React from 'react';

export function PillItem(props: any) {
  return (
    <div
      className={`pill-item ${props.checked ? 'checked' : ''}`}
      title={props.title}
      onClick={event => props.onClick(event)}
    >
      {props.label}
    </div>
  );
}
