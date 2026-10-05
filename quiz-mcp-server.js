#!/usr/bin/env node

/**
 * Quiz MCP Server for Claude Code
 *
 * Provides server-side quiz grading, shuffling, and answer storage.
 * Questions are shuffled, grades are server-side, answers are never logged.
 *
 * Usage: node quiz-mcp-server.js
 */

const readline = require('readline');

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
  terminal: false,
});

// Store active quizzes by ID
const quizzes = {};
let quizCounter = 0;

const send = (obj) => {
  console.log(JSON.stringify(obj));
};

/**
 * Shuffle an array in-place
 */
function shuffle(arr) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

rl.on('line', (line) => {
  try {
    const msg = JSON.parse(line);

    if (msg.method === 'initialize') {
      send({
        jsonrpc: '2.0',
        id: msg.id,
        result: {
          protocolVersion: '2024-11-05',
          capabilities: {
            tools: {},
          },
          serverInfo: {
            name: 'quiz-server',
            version: '1.0.0',
          },
        },
      });
    } else if (msg.method === 'tools/list') {
      send({
        jsonrpc: '2.0',
        id: msg.id,
        result: {
          tools: [
            {
              name: 'quiz',
              description: 'Present a multiple-choice quiz question with server-side grading and shuffling',
              inputSchema: {
                type: 'object',
                properties: {
                  question: {
                    type: 'string',
                    description: 'The quiz question text',
                  },
                  options: {
                    type: 'array',
                    items: { type: 'string' },
                    description: 'Answer options (2-4 items)',
                  },
                  correctIndex: {
                    type: 'number',
                    description: 'Index of the correct answer (hidden from model)',
                  },
                  explanation: {
                    type: 'string',
                    description: 'Explanation shown after grading',
                  },
                  note: {
                    type: 'string',
                    description: 'Optional note about this question',
                  },
                },
                required: ['question', 'options', 'correctIndex', 'explanation'],
              },
            },
            {
              name: 'quiz_submit',
              description: 'Submit an answer to a quiz question for server-side grading',
              inputSchema: {
                type: 'object',
                properties: {
                  quizId: {
                    type: 'string',
                    description: 'ID of the quiz question',
                  },
                  selectedIndex: {
                    type: 'number',
                    description: 'Index of the selected option',
                  },
                },
                required: ['quizId', 'selectedIndex'],
              },
            },
          ],
        },
      });
    } else if (msg.method === 'tools/call') {
      const { name, arguments: args } = msg.params;

      if (name === 'quiz') {
        // Register quiz and return shuffled options (without revealing correct answer)
        const quizId = `quiz_${++quizCounter}_${Date.now()}`;

        // Create shuffled mapping: original index -> new index
        const optionIndices = args.options.map((_, i) => i);
        const shuffledIndices = shuffle([...optionIndices]);
        const newCorrectIndex = shuffledIndices.indexOf(args.correctIndex);

        // Store quiz server-side
        quizzes[quizId] = {
          question: args.question,
          options: args.options,
          correctIndex: args.correctIndex,
          newCorrectIndex: newCorrectIndex,
          shuffledIndices: shuffledIndices,
          explanation: args.explanation,
          note: args.note || '',
          answered: false,
        };

        // Return shuffled options to model
        const shuffledOptions = shuffledIndices.map(i => args.options[i]);

        send({
          jsonrpc: '2.0',
          id: msg.params.tool_use_id,
          result: {
            content: [
              {
                type: 'text',
                text: `Question registered (ID: ${quizId}). Options have been shuffled for fairness.\n\nQuestion: ${args.question}\n\nOptions:\n${shuffledOptions.map((opt, i) => `${i + 1}. ${opt}`).join('\n')}\n\nAsk the user to select an option, then call quiz_submit with the selected index.`,
              },
            ],
          },
        });
      } else if (name === 'quiz_submit') {
        // Grade the submission server-side
        const quiz = quizzes[args.quizId];

        if (!quiz) {
          send({
            jsonrpc: '2.0',
            id: msg.params.tool_use_id,
            result: {
              content: [
                {
                  type: 'text',
                  text: `Error: quiz not found (ID: ${args.quizId})`,
                },
              ],
            },
          });
          return;
        }

        // Map selected index back to original index
        const originalSelectedIndex = quiz.shuffledIndices[args.selectedIndex];
        const isCorrect = originalSelectedIndex === quiz.correctIndex;
        quiz.answered = true;
        quiz.userSelectedIndex = originalSelectedIndex;

        // Return grade and explanation
        const feedback = isCorrect
          ? `✅ Correct!\n\n${quiz.explanation}`
          : `❌ Not quite. The correct answer is: **${quiz.options[quiz.correctIndex]}**\n\n${quiz.explanation}`;

        send({
          jsonrpc: '2.0',
          id: msg.params.tool_use_id,
          result: {
            content: [
              {
                type: 'text',
                text: feedback,
              },
            ],
          },
        });
      }
    }
  } catch (err) {
    console.error(`[MCP Error] ${err.message}`);
  }
});

// Signal ready
send({
  jsonrpc: '2.0',
  method: 'initialized',
});
