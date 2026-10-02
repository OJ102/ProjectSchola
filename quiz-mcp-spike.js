#!/usr/bin/env node

// Minimal MCP server for spike testing: quiz with form-based response
// Tests whether MCP form elicitation works for quiz UI
// Usage: node quiz-mcp-spike.js

const readline = require('readline');

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
  terminal: false,
});

let requestId = 1;

const send = (obj) => {
  console.log(JSON.stringify(obj));
};

rl.on('line', (line) => {
  try {
    const msg = JSON.parse(line);

    if (msg.method === 'initialize') {
      // Respond to initialize
      send({
        jsonrpc: '2.0',
        id: msg.id,
        result: {
          protocolVersion: '2024-11-05',
          capabilities: {
            tools: {},
          },
          serverInfo: {
            name: 'quiz-spike',
            version: '0.0.1',
          },
        },
      });
    } else if (msg.method === 'tools/list') {
      // List available tools
      send({
        jsonrpc: '2.0',
        id: msg.id,
        result: {
          tools: [
            {
              name: 'quiz',
              description: 'Present a multiple-choice quiz question',
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
                    description: 'List of answer options (2-4)',
                  },
                  correctIndex: {
                    type: 'number',
                    description: 'Index of the correct answer',
                  },
                  explanation: {
                    type: 'string',
                    description: 'Explanation shown after user answers',
                  },
                },
                required: ['question', 'options', 'correctIndex', 'explanation'],
              },
            },
          ],
        },
      });
    } else if (msg.method === 'tools/call') {
      const { name, arguments: args } = msg.params;

      if (name === 'quiz') {
        // For now, return a simple text response indicating the form was presented
        // In a real scenario, Claude Code would present an elicitation form
        send({
          jsonrpc: '2.0',
          id: msg.id,
          result: {
            content: [
              {
                type: 'text',
                text: `[QUIZ SPIKE] Question: "${args.question}"\nOptions: ${args.options.join(', ')}\n[Awaiting user response...]`,
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

// Signal that server is ready
send({
  jsonrpc: '2.0',
  method: 'initialized',
});
