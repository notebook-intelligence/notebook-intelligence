// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React, { useState } from 'react';

export function AskUserQuestion(props: any) {
  const userQuestions = props.userQuestions.content;
  const [selectedAnswers, setSelectedAnswers] = useState<{
    [key: string]: string[];
  }>({});

  const onOptionSelected = (question: any, option: any) => {
    if (question.multiSelect) {
      if (selectedAnswers[question.question]?.includes(option.label)) {
        setSelectedAnswers({
          ...selectedAnswers,
          [question.question]: (
            selectedAnswers[question.question] ?? []
          ).filter((o: any) => o !== option.label)
        });
      } else {
        setSelectedAnswers({
          ...selectedAnswers,
          [question.question]: [
            ...(selectedAnswers[question.question] ?? []),
            option.label
          ]
        });
      }
    } else {
      setSelectedAnswers({
        ...selectedAnswers,
        [question.question]: [option.label]
      });
    }
  };

  return (
    <>
      {userQuestions.title ? (
        <div>
          <b>{userQuestions.title}</b>
        </div>
      ) : null}
      {userQuestions.message ? <div>{userQuestions.message}</div> : null}
      {userQuestions.questions.map((question: any) => (
        <div className="ask-user-question-container" key={question.question}>
          <form className="ask-user-question-form">
            <div className="ask-user-question-question">
              {question.question}
            </div>
            <div className="ask-user-question-header">{question.header}</div>
            <div className="ask-user-question-options">
              {question.options.map((option: any) => (
                <div className="ask-user-question-option" key={option.label}>
                  <div className="ask-user-question-option-input-container">
                    <input
                      id={option.label}
                      type="checkbox"
                      checked={
                        selectedAnswers[question.question]?.includes(
                          option.label
                        ) ?? false
                      }
                      onChange={() => onOptionSelected(question, option)}
                    />
                    <label
                      htmlFor={option.label}
                      className="ask-user-question-option-label-container"
                    >
                      <div className="ask-user-question-option-label">
                        {option.label}
                      </div>
                      <div className="ask-user-question-option-description">
                        {option.description}
                      </div>
                    </label>
                  </div>
                </div>
              ))}
            </div>
          </form>
        </div>
      ))}
      <div className="ask-user-question-footer">
        <button
          className="jp-Dialog-button jp-mod-accept jp-mod-styled"
          onClick={() => {
            props.onSubmit(selectedAnswers);
          }}
        >
          <div className="jp-Dialog-buttonLabel">
            {userQuestions.submitLabel}
          </div>
        </button>
        <button
          className="jp-Dialog-button jp-mod-reject jp-mod-styled"
          onClick={() => {
            props.onCancel();
          }}
        >
          <div className="jp-Dialog-buttonLabel">
            {userQuestions.cancelLabel}
          </div>
        </button>
      </div>
    </>
  );
}
