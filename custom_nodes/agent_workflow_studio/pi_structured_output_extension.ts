import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const TOOL_NAME = "agent_workflow_submit_json";
const SCHEMA_ENV = "PI_STRUCTURED_OUTPUT_SCHEMA";

function outputParameters() {
  const schemaText = process.env[SCHEMA_ENV];
  if (!schemaText) {
    throw new Error("Structured output schema is missing");
  }
  const schema: unknown = JSON.parse(schemaText);
  if (typeof schema !== "object" || schema === null || Array.isArray(schema)) {
    throw new Error("Structured output schema must be a JSON object");
  }
  return Type.Unsafe(schema);
}

export default function (pi: ExtensionAPI) {
  pi.registerTool(
    defineTool({
      name: TOOL_NAME,
      label: "Submit typed result",
      description:
        "Return the complete result using the supplied parameter schema. This tool only captures structured data; it performs no external actions.",
      promptSnippet: "Submit the final result using the schema-bound typed output tool.",
      promptGuidelines: [
        "Use agent_workflow_submit_json exactly once to return the complete final result.",
        "Do not emit a separate assistant text response.",
      ],
      parameters: outputParameters(),
      constrainedSampling: { type: "json_schema", strict: "prefer" },
      async execute() {
        return {
          content: [{ type: "text", text: "Structured result accepted." }],
          details: undefined,
          terminate: true,
        };
      },
    }),
  );
}

export { TOOL_NAME as STRUCTURED_OUTPUT_TOOL_NAME };
