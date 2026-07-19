export default function Loading() {
  return (
    <div className="flex h-screen w-full items-center justify-center">
      <div className="flex flex-col items-center space-y-4">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-neutral-300 border-t-blue-600"></div>
        <p className="text-sm font-medium text-neutral-500">Loading platform...</p>
      </div>
    </div>
  );
}
