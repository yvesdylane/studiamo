# Studiamo Markdown

Notes, exercises and exams are written in **Markdown**, the plain-text format
used on GitHub, plus a few **interactive blocks**. This page is written in it,
so every example below is live: try them.

## Plain Markdown

```
# Big title (one per note)
## Section (shows in "On this page")
### Smaller heading

**bold**, *italic*, `code`, [a link](https://example.com)

- a list
- of items

1. numbered
2. steps

> a quote

| Name  | Score |
|-------|-------|
| Amina | 15    |
```

Raw HTML is not allowed (it is shown as text), so a note can never run
anything on the reader's computer except the blocks below.

## The code box

The simplest way to add code: three double quotes, the language and an
optional title; close with three double quotes alone on a line.

```
"""python Try it yourself
names = ["Amina", "Bello"]
print(len(names))
"""
```

"""python Try it yourself
names = ["Amina", "Bello"]
print(len(names))
"""

Python and JavaScript get a **Run** button. Any other language is shown as
code:

"""c Hello in C
#include <stdio.h>
int main(void) { printf("hello\n"); }
"""

Start the box with `--- file` lines and it becomes a small IDE (see below).

## Runnable code (long form)

Write `run` and the language (`python` or `javascript`), and optionally a title
in quotes. Readers can edit the code and press **Run** (or Ctrl+Enter).

````
```run python "Try it yourself"
names = ["Amina", "Bello", "Chloé"]
for i, name in enumerate(names, 1):
    print(i, name)
```
````

```run python "Try it yourself"
names = ["Amina", "Bello", "Chloé"]
for i, name in enumerate(names, 1):
    print(i, name)
```

```run javascript "Functions are values"
const applyTwice = (fn, v) => fn(fn(v));
console.log(applyTwice(n => n + 10, 5));
```

Code runs in the reader's browser, sealed off from the site, and is stopped
after 10 seconds. Python's `input()` is not available (it reads nothing).

## A small IDE: several files

Use `ide` and the language. Start each file with a line `--- name`. The first
file runs, unless you name another one with `entry:` on the first line.
Readers get a file list, can open and change every file, and run the project.

````
```ide python "A tiny package"
entry: main.py
--- main.py
from shapes.area import square
print(square(4))
--- shapes/__init__.py
--- shapes/area.py
def square(side):
    return side * side
```
````

```ide python "A tiny package"
entry: main.py
--- main.py
from shapes.area import square
print(square(4))
--- shapes/__init__.py
--- shapes/area.py
def square(side):
    return side * side
```

In JavaScript, files import each other with relative paths:

```ide javascript "Modules"
--- app.js
import { double } from "./utils.js";
console.log([1, 2, 3].map(double));
--- utils.js
export const double = n => n * 2;
```

## File tree

Indent with spaces. Folders end with `/`. Add a remark after `<-`.

````
```files "my-project"
my-project/
  src/
    app.js   <- we edit this
    utils.js
  README.md
```
````

```files "my-project"
my-project/
  src/
    app.js   <- we edit this
    utils.js
  README.md
```

## Live diagrams

Boxes and arrows the reader can **drag around** (mouse, touch, or the arrow
keys). A box can hold a **number the reader can change**, or a **formula**
of other boxes that updates at once, so readers see what each change affects.

One line per box or arrow:

| Line                     | Meaning                                   |
|--------------------------|-------------------------------------------|
| `id: Label`              | a plain box                               |
| `id: Label = 20`         | a number the reader can change            |
| `id: Label = a * b`      | a formula of other boxes (live)           |
| `a -> b`                 | an arrow (formulas draw their own arrows) |
| `a -> b : why`           | an arrow with a label                     |
| `... @ 40, 120`          | place the box at x, y (else automatic)    |
| `// remark`              | ignored                                   |

Formulas can use `+ - * / % ^`, brackets, and `min max abs sqrt round floor ceil pow`.

**Arrange it by dragging.** In the note editor's preview, drag the boxes where you
want them: each box's line gets its position (`@ x, y`) and the note keeps that
layout (Ctrl+Z undoes a move). Readers see your layout, shrunk proportionally
when their screen is narrower. Readers can drag too, to explore, but their moves
are not saved. Boxes without a position are placed automatically, one column
per step of arrows.

````
```diagram "A shopping cart"
price: Price = 20
qty: Quantity = 3
subtotal: Subtotal = price * qty
tax: Tax (20%) = subtotal * 0.2
total: Total = subtotal + tax
```
````

```diagram "A shopping cart"
price: Price = 20
qty: Quantity = 3
subtotal: Subtotal = price * qty
tax: Tax (20%) = subtotal * 0.2
total: Total = subtotal + tax
```

Plain boxes work for any picture of how things connect:

```diagram "Request flow"
browser: Browser
server: Flask app
db: Database
browser -> server : request
server -> db : query
```

## Highlighted boxes

`note`, `tip` or `warning`, with Markdown inside. A title in quotes is optional.

````
```tip "Remember"
A **pure** function always returns the same output for the same input.
```
````

```tip "Remember"
A **pure** function always returns the same output for the same input.
```

## Side by side

`compare` with two titles, and `---` between the two snippets.

````
```compare "Changing in place" | "Making a copy"
student.score = 15
---
const updated = { ...student, score: 15 };
```
````

```compare "Changing in place" | "Making a copy"
student.score = 15
---
const updated = { ...student, score: 15 };
```
