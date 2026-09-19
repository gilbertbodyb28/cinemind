import { createContext, useContext } from "react";

export const SearchContext = createContext({ q: "", setQ: () => {} });
export const useSearch = () => useContext(SearchContext);
