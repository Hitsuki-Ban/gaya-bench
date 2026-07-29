export interface DirectoryFile {
  readonly name: string;
  readonly webkitRelativePath: string;
  arrayBuffer(): Promise<ArrayBuffer>;
}

export interface ObjectUrlFactory {
  create(file: DirectoryFile): string;
  revoke(url: string): void;
}
