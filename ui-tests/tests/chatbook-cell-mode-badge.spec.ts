/**
 * The NL/Py badge is positioned against JupyterLab's own cell toolbar, whose
 * metrics live in lab's CSS rather than ours. These checks pin the resulting
 * geometry so a lab upgrade that moves the toolbar shows up here instead of as
 * a misaligned badge.
 */
import { expect, test } from '@jupyterlab/galata';

interface IBox {
  top: number;
  right: number;
  bottom: number;
  left: number;
  width: number;
  height: number;
}

test('mode badge lines up with the cell toolbar', async ({ page }) => {
  await page.notebook.createNew();
  await page.notebook.setCell(0, 'code', 'print("hello world")');
  await page.locator('.jp-Notebook .jp-Cell').first().click();
  await expect(page.locator('.jp-cell-toolbar')).toHaveCount(1);

  const boxes = await page.evaluate(() => {
    const cell = document.querySelector('.jp-Notebook .jp-Cell');
    if (!cell) {
      throw new Error('no cell rendered');
    }
    // Stand in for a Chatbook cell: the extension adds this class and button
    // only for notebooks bound to the chatbook kernel.
    cell.classList.add('nbi-chatbook-cell-prompt');
    const button = document.createElement('button');
    button.className = 'nbi-chatbook-cell-mode';
    button.type = 'button';
    button.textContent = 'NL';
    (cell.querySelector('.jp-InputArea') ?? cell).appendChild(button);

    const box = (node: Element | null) => {
      if (!node) {
        return null;
      }
      const { top, right, bottom, left, width, height } =
        node.getBoundingClientRect();
      return { top, right, bottom, left, width, height };
    };
    return {
      inputArea: box(cell.querySelector('.jp-InputArea')),
      toolbarIcon: box(
        cell.querySelector('.jp-cell-toolbar .jp-ToolbarButtonComponent svg')
      ),
      badge: box(button)
    };
  });

  const inputArea = boxes.inputArea as IBox;
  const toolbarIcon = boxes.toolbarIcon as IBox;
  const badge = boxes.badge as IBox;
  const centerY = (box: IBox) => (box.top + box.bottom) / 2;

  // Same optical line as the toolbar icons.
  expect(Math.abs(centerY(badge) - centerY(toolbarIcon))).toBeLessThanOrEqual(
    1
  );
  // Inside the input row, so the input area's `overflow: hidden` never clips it.
  expect(badge.top).toBeGreaterThanOrEqual(inputArea.top);
  expect(badge.bottom).toBeLessThanOrEqual(inputArea.bottom);
  // Right-aligned with the input area, with the toolbar kept clear to its left.
  expect(inputArea.right - badge.right).toBeLessThanOrEqual(6);
  expect(badge.left).toBeGreaterThan(toolbarIcon.right);
});
