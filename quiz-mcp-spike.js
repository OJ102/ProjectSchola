#!/usr/bin/env node
const readline = require('readline');

// Minimal MCP server for spike testing: quiz with elicitation form
// Usage: node quiz-mcp-spike.js
// The server reads JSON-RPC from stdin and writes responses to stdout.

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
let messageId = 0;

const send = (obj) => console.log(JSON.stringify(obj));

// Initialize on start
const init = () => {
  send({
    jsonrpc: '2.0',
    id: messageId++,
    method: 'initialize',
    params: {
      protocolVersion: '2024-11-05',
      capabilities: {
        tools: {},
        resources: {},
        prompts: {},
        roots: [],
      },
      clientInfo: {
        name: 'Claude Code',
        version: '1.0.0',
      },
    },
  });
};

// Listen for requests
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
            tools: {
              listChanged: false,
            },
          },
          serverInfo: {
            name: 'quiz-spike',
            version: '0.0.1',
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
              name: 'ask_quiz',
              description: 'Ask a quiz question with elicitation form',
              inputSchema: {
                type: 'object',
                properties: {
                  question: {
                    type: 'string',
                    description: 'The quiz question',
                  },
                  options: {
                    type: 'array',
                    items: { type: 'string' },
                    description: 'Answer options',
                  },
                  correctAnswerIndex: {
                    type: 'number',
                    description: 'Index of correct answer (hidden from model)',
                  },
                  explanation: {
                    type: 'string',
                    description: 'Explanation for the answer',
                  },
                },
                required: ['question', 'options', 'correctAnswerIndex', 'explanation'],
              },
            },
          ],
        },
      });
    } else if (msg.method === 'tools/call') {
      const { name, arguments: args } = msg.params;

      if (name === 'ask_quiz') {
        // Return an elicitation form instead of a direct tool response
        send({
          jsonrpc: '2.0',
          id: msg.id,
          result: {
            type: 'elicitation',
            elicitationId: `quiz-${Date.now()}`,
            form: {
              title: 'Quiz Question',
              sections: [
                {
                  title: 'Question',
                  type: 'section',
                  content: args.question,
                },
                {
                  title: 'Your Answer',
                  fields: [
                    {
                      name: 'answer',
                      type: 'select',
                      label: 'Select your answer',
                      options: args.options.map((opt, i) => ({ value: String(i), label: opt })),
                      required: true,
                    },
                    {
                      name: 'note',
                      type: 'text',
                      label: 'Notes (optional)',
                      multiline: true,
                      required: false,
                    },
                  ],
                },
              ],
            },
          },
        });
      }
    } else if (msg.method === 'elicitation/submit') {
      // Handle elicitation form submission
      const { elicitationId, formData } = msg.params;
      const selectedIndex = parseInt(formData.answer);

      send({
        jsonrpc: '2.0',
        id: msg.id,
        result: {
          type: 'toolResult',
          content: [
            {
              type: 'text',
              text: `Selected option ${selectedIndex}. Grading on server side.`,
            },
          ],
        },
      });
    }
  } catch (err) {
    console.error('Error:', err.message);
  }
});

// Send initial capabilities on start
send({
  jsonrpc: '2.0',
  method: 'initialized',
});
