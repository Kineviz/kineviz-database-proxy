import type { DatabaseType, Project, ProjectCreate } from "../../types/project";

/**
 * A store the user picked somewhere else, opened here.
 *
 * The Files page has already read the file's header, so it knows both the path and
 * which engine wrote it — carrying both across means the form opens on the right
 * type instead of asking a question that has already been answered.
 */
export interface StorePrefill {
  path: string;
  database_type?: DatabaseType | null;
}

export interface ProjectFormProps {
  initialValues?: Project | null;
  /** Only read when creating: an existing project's own config always wins. */
  prefill?: StorePrefill;
  open: boolean;
  onSubmit: (values: ProjectCreate) => void;
  onCancel: () => void;
}

// The embedded engines are files rather than servers: no host, no port, no
// credentials. Keep this in step with EMBEDDED_ENGINES in types/project.ts.
export const EMBEDDED_TYPES: DatabaseType[] = ["kuzu", "ladybug", "latticedb"];
export const isEmbedded = (type: DatabaseType) => EMBEDDED_TYPES.includes(type);
