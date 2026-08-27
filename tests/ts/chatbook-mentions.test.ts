// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  applyChatbookMention,
  detectChatbookMentionTrigger,
  isChatbookMentionMenuKey
} from '../../src/chatbook-mentions';

describe('chatbook filesystem mentions', () => {
  it('detects a mention at start or after whitespace', () => {
    expect(detectChatbookMentionTrigger('@dat', 4)).toEqual({
      from: 0,
      to: 4,
      query: 'dat'
    });
    expect(detectChatbookMentionTrigger('load @docs/gu', 13)).toEqual({
      from: 5,
      to: 13,
      query: 'docs/gu'
    });
  });

  it('does not treat email addresses or completed mentions as triggers', () => {
    expect(detectChatbookMentionTrigger('person@example.com', 18)).toBeNull();
    expect(
      detectChatbookMentionTrigger('use @file:data.csv next', 23)
    ).toBeNull();
  });

  it('replaces the active query with an opaque mention token', () => {
    const text = 'load @dat then plot';
    const trigger = detectChatbookMentionTrigger(text, 9);
    expect(trigger).not.toBeNull();
    expect(applyChatbookMention(text, trigger!, 'file:data.csv')).toBe(
      'load @file:data.csv then plot'
    );
  });

  it('claims navigation keys but leaves modified shortcuts alone', () => {
    const key = (
      name: string,
      modifiers: Partial<KeyboardEvent> = {}
    ): Parameters<typeof isChatbookMentionMenuKey>[0] => ({
      key: name,
      altKey: false,
      ctrlKey: false,
      metaKey: false,
      shiftKey: false,
      ...modifiers
    });
    expect(isChatbookMentionMenuKey(key('Tab'))).toBe(true);
    expect(isChatbookMentionMenuKey(key('Enter'))).toBe(true);
    expect(isChatbookMentionMenuKey(key('ArrowDown'))).toBe(true);
    expect(isChatbookMentionMenuKey(key('Tab', { shiftKey: true }))).toBe(
      false
    );
    expect(isChatbookMentionMenuKey(key('Enter', { shiftKey: true }))).toBe(
      false
    );
    expect(isChatbookMentionMenuKey(key('a'))).toBe(false);
  });
});
